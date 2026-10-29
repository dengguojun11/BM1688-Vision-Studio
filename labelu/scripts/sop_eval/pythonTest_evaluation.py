import os
import sys
import cv2
import json
import time
import threading
import numpy as np

from ctypes import cdll, byref, c_int, c_ubyte, c_float, c_char_p, c_void_p, c_uint32, c_bool, Structure, POINTER, c_uint8, cast, pointer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evaluation_utils import (
    Detection,
    SIZE_BINS,
    SIZE_BIN_DESC,
    _empty_stats,
    attach_scene_attrs,
    get_cpu_memory_mb,
    iou,
    load_dataset_from_yaml,
    load_yolo_labels,
    match_detections_grouped,
    summarize_metrics,
    summarize_scene_metrics,
    summarize_size_metrics,
)

# ── 评估配置 ──────────────────────────────────────────────────
EVAL_DATA_PATH = os.getenv("EVAL_DATA_PATH", "./data/")
EVAL_XML_DIR = os.getenv("EVAL_XML_DIR", "./data/xml/test")
EVAL_SAVE_DIR = os.getenv("EVAL_SAVE_DIR", "./results_bmodel")
EVAL_IOU_THRES = float(os.getenv("EVAL_IOU_THRES", "0.5"))
EVAL_CONF_THRES = float(os.getenv("EVAL_CONF_THRES", "0.35"))
MODEL_PATH = os.getenv("EVAL_MODEL_PATH", "./model/")
RESULT_SAVE = os.getenv("RESULT_SAVE", "./results/")
BMODEL_NMS_THRES = 0.5
DUMP_PREDICTIONS = os.getenv("EVAL_DUMP_PREDICTIONS", "1") != "0"
EVAL_INPUT_COLOR = os.getenv("EVAL_INPUT_COLOR", "bgr").lower()
# ─────────────────────────────────────────────────────────────


DEFAULT_CLASS_NAMES = ['garbage_street_scattered',
                       'garbage_street_can',
                       'garbage_street_dump',
                       'object_box',
                       'garbage_street_pack',
                       'non_garbage']



def _env_class_names(default_names):
    raw = os.getenv("EVAL_CLASS_NAMES", "").strip()
    if not raw:
        return list(default_names)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return [parsed[k] for k in sorted(parsed, key=lambda x: int(x))]
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except Exception:
        pass
    return [x.strip() for x in raw.split(",") if x.strip()]


def resolve_class_names(dataset_class_names):
    class_names = _env_class_names(DEFAULT_CLASS_NAMES)
    if class_names:
        return class_names
    return list(dataset_class_names)


def resolve_ai_lib_path():
    lib_path = os.getenv("AI_LIB_PATH", "/usr/lib/librknnx_api.so.3.4")
    if os.path.exists(lib_path):
        return lib_path

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_model_train_dir = os.path.abspath(os.path.join(script_dir, "..", ".."))
    local_path = os.path.join(
        repo_model_train_dir,
        "ai_services_libs_v3.8",
        "ai_services_libs_v3.8",
        "librknnx_api.so.3.4",
    )
    if os.path.exists(local_path):
        return local_path
    return lib_path


def _first_present(item, keys, default=None):
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return default


def parse_alarm_predictions(alarm_list, class_name_to_id, img_w, img_h):
    predictions = []
    for item in alarm_list:
        class_name = _first_present(item, ("className", "class_name", "name", "label"), "")
        cid = class_name_to_id.get(class_name, -1)
        if cid < 0:
            continue

        score = float(_first_present(item, ("similarity", "score", "confidence", "conf", "prob"), 1.0))
        if score < EVAL_CONF_THRES:
            continue

        x1 = float(_first_present(item, ("X1", "x1", "left", "xmin"), 0.0))
        y1 = float(_first_present(item, ("Y1", "y1", "top", "ymin"), 0.0))
        x2 = float(_first_present(item, ("X2", "x2", "right", "xmax"), 0.0))
        y2 = float(_first_present(item, ("Y2", "y2", "bottom", "ymax"), 0.0))

        x1 = max(0.0, min(float(img_w), x1))
        x2 = max(0.0, min(float(img_w), x2))
        y1 = max(0.0, min(float(img_h), y1))
        y2 = max(0.0, min(float(img_h), y2))
        if x2 <= x1 or y2 <= y1:
            continue

        predictions.append(Detection(x1=x1, y1=y1, x2=x2, y2=y2, class_id=cid, score=score))
    return predictions


def prediction_record(sample, predictions, class_names, img_w, img_h):
    boxes = []
    for det in predictions:
        class_name = class_names[det.class_id] if 0 <= det.class_id < len(class_names) else str(det.class_id)
        boxes.append({
            "class_id": int(det.class_id),
            "class_name": class_name,
            "score": float(det.score),
            "x1": float(det.x1),
            "y1": float(det.y1),
            "x2": float(det.x2),
            "y2": float(det.y2),
        })
    return {
        "image": sample.image_path,
        "image_name": os.path.basename(sample.image_path),
        "width": int(img_w),
        "height": int(img_h),
        "boxes": boxes,
    }


def _class_name(class_names, class_id):
    return class_names[class_id] if 0 <= class_id < len(class_names) else str(class_id)


def _det_text(det, class_names):
    return (
        f'{_class_name(class_names, det.class_id)} '
        f'score={det.score:.4f} '
        f'box=({det.x1:.1f},{det.y1:.1f},{det.x2:.1f},{det.y2:.1f})'
    )


def image_match_detail(sample, predictions, gt_boxes, class_names, class_count, iou_threshold):
    pred_by_class = [[] for _ in range(class_count)]
    gt_by_class = [[] for _ in range(class_count)]
    for pred in predictions:
        if 0 <= pred.class_id < class_count:
            pred_by_class[pred.class_id].append(pred)
    for gt in gt_boxes:
        if 0 <= gt.class_id < class_count:
            gt_by_class[gt.class_id].append(gt)

    matched = []
    fps = []
    fns = []

    for cid in range(class_count):
        preds = sorted(pred_by_class[cid], key=lambda item: -item.score)
        gts = gt_by_class[cid]
        gt_used = [False] * len(gts)

        for pred in preds:
            best_iou = 0.0
            best_idx = -1
            for idx, gt in enumerate(gts):
                if gt_used[idx]:
                    continue
                cur_iou = iou(pred, gt)
                if cur_iou > best_iou:
                    best_iou = cur_iou
                    best_idx = idx

            if best_idx >= 0 and best_iou >= iou_threshold:
                gt_used[best_idx] = True
                matched.append((pred, gts[best_idx], best_iou))
            else:
                fps.append((pred, best_iou))

        for idx, gt in enumerate(gts):
            if gt_used[idx]:
                continue
            best_pred_iou = 0.0
            for pred in preds:
                best_pred_iou = max(best_pred_iou, iou(pred, gt))
            fns.append((gt, best_pred_iou))

    return {
        "image_path": sample.image_path,
        "image_name": os.path.basename(sample.image_path),
        "gt": len(gt_boxes),
        "pred": len(predictions),
        "tp": len(matched),
        "fp": len(fps),
        "fn": len(fns),
        "matched": matched,
        "fps": fps,
        "fns": fns,
    }


def write_image_details(save_dir, image_details, class_names):
    path = os.path.join(save_dir, "image_details.txt")
    NL = "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write("YOLOv11 per-image detail report" + NL)
        f.write("-" * 80 + NL)
        for detail in image_details:
            f.write(
                f'image={detail["image_path"]} | '
                f'gt={detail["gt"]} pred={detail["pred"]} '
                f'tp={detail["tp"]} fp={detail["fp"]} fn={detail["fn"]}' + NL
            )

            if detail["fns"]:
                f.write("  FN missed ground truth:" + NL)
                for gt, best_iou in detail["fns"]:
                    f.write(f"    - {_det_text(gt, class_names)} best_pred_iou={best_iou:.4f}" + NL)

            if detail["fps"]:
                f.write("  FP extra predictions:" + NL)
                for pred, best_iou in detail["fps"]:
                    f.write(f"    - {_det_text(pred, class_names)} best_gt_iou={best_iou:.4f}" + NL)

            f.write(NL)
    return path


def append_image_summary(report_path, image_details, image_detail_path):
    problem_images = [item for item in image_details if item["fp"] > 0 or item["fn"] > 0]
    NL = "\n"
    with open(report_path, "a", encoding="utf-8") as f:
        f.write(NL)
        f.write("【逐图片问题摘要】" + NL)
        f.write(f"  详细文件: {image_detail_path}" + NL)
        f.write(f"  问题图片数: {len(problem_images)} / {len(image_details)}" + NL)
        f.write(f'  {"图片路径":<80} {"GT":>4} {"Pred":>5} {"TP":>4} {"FP":>4} {"FN":>4}' + NL)
        f.write("  " + "-" * 110 + NL)
        for item in problem_images:
            f.write(
                f'  {item["image_path"]:<80} {item["gt"]:>4} {item["pred"]:>5}'
                f' {item["tp"]:>4} {item["fp"]:>4} {item["fn"]:>4}' + NL
            )


def write_txt_report(save_dir, summary, thresholds=None, scene_summary=None, size_summary=None):
    os.makedirs(save_dir, exist_ok=True)
    report_path = os.path.join(save_dir, 'evaluation_report.txt')
    NL = '\n'
    SEP = '-' * 60 + NL

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('YOLOv11 边缘评估报告' + NL)
        f.write(SEP)

        if thresholds:
            f.write('【评估配置】' + NL)
            for k, v in thresholds.items():
                f.write(f'  {k}: {v}' + NL)
            f.write(NL)

        f.write('【全局指标】' + NL)
        f.write(f'  评估图片数  : {summary["images"]}' + NL)
        f.write(f'  总耗时      : {summary["time_sec"]:.2f}s' + NL)
        f.write(f'  平均推理耗时: {summary["speed_avg_ms"]:.2f}ms/张' + NL)
        f.write(f'  mAP50       : {summary["map50"]:.4f}' + NL)
        if summary.get('map5095_status') == 'not_computed':
            f.write('  mAP50-95    : not_computed (仅计算 IoU=0.50 的 AP)' + NL)
            f.write(
                f'  AP50复用参考: {summary.get("map5095_approximation", 0):.4f} '
                '(非 mAP50-95)' + NL
            )
        else:
            f.write(f'  mAP50-95    : {summary["map5095"]:.4f}' + NL)
        f.write(f'  整体精确率  : {summary["overall_precision"]:.4f}' + NL)
        f.write(f'  整体召回率  : {summary["overall_recall"]:.4f}' + NL)
        f.write(f'  整体F1      : {summary["overall_f1"]:.4f}' + NL)
        if summary.get('cpu_memory_mb'):
            f.write(f'  CPU内存     : {summary["cpu_memory_mb"]:.1f}MB' + NL)
        for k, v in summary.get('extra_speed_metrics', {}).items():
            f.write(f'  {k}: {v:.2f}' + NL)
        f.write(NL)

        f.write('【各类别指标】' + NL)
        f.write(f'  {"类别":<30} {"标签数":>6} {"TP":>6} {"FP":>6} {"FN":>6} {"精确率":>8} {"召回率":>8} {"F1":>8} {"AP50":>8}' + NL)
        f.write('  ' + '-' * 90 + NL)
        for row in summary['per_class_rows']:
            f.write(
                f'  {row["name"]:<30} {row["labels"]:>6} {row["tp"]:>6} {row["fp"]:>6} {row["fn"]:>6}'
                f' {row["precision"]:>8.4f} {row["recall"]:>8.4f} {row["f1"]:>8.4f} {row["ap50"]:>8.4f}' + NL
            )
        f.write(NL)

        if size_summary:
            f.write('【尺寸分档指标】  (r = 目标面积 / 图像面积)' + NL)
            for bin_name, bin_data in size_summary.items():
                desc = SIZE_BIN_DESC.get(bin_name, '')
                f.write(f'  [{bin_name}]  {desc}  mAP50={bin_data["map50"]:.4f}' + NL)
                f.write(f'  {"类别":<30} {"精确率":>8} {"召回率":>8} {"F1":>8}' + NL)
                for cls in bin_data['per_class']:
                    f.write(f'  {cls["name"]:<30} {cls["precision"]:>8.4f} {cls["recall"]:>8.4f} {cls["f1"]:>8.4f}' + NL)
                f.write(NL)

        if scene_summary:
            f.write('【场景维度指标】' + NL)
            for field_key, val_dict in scene_summary.items():
                f.write(f'  == {field_key} ==' + NL)
                for val, val_data in val_dict.items():
                    f.write(f'  [{val}]  mAP50={val_data["map50"]:.4f}' + NL)
                    f.write(f'  {"类别":<30} {"精确率":>8} {"召回率":>8} {"F1":>8}' + NL)
                    for cls in val_data['per_class']:
                        f.write(f'  {cls["name"]:<30} {cls["precision"]:>8.4f} {cls["recall"]:>8.4f} {cls["f1"]:>8.4f}' + NL)
                    f.write(NL)

    return report_path

class DecodeDataRgb(Structure):
    """ creates a struct to match decode_data_st_t in c lib """
    _fields_ = [
        ('width', c_int),
        ('height', c_int),
        ('rgb_len', c_int),
        ('rgb_buf', POINTER(c_uint8)),
    ]
    def __str__(self):
        result = f'rgb_len:{self.rgb_len},width:{self.width},height:{self.height},'
        return result

class AILib:
    def __init__(self):
        self.ai_lib_path = resolve_ai_lib_path()
        self.ai_lib = cdll.LoadLibrary(self.ai_lib_path)
        # 1)creat_rknn_model_engine 函数说明
        # 输出
        self.ai_lib.creat_rknn_model_engine.restype = c_void_p
        self.ai_lib.creat_rknn_model_engine.argtypes = [c_char_p, c_float]

        # 2)init_rknn_model_engine 函数说明
        self.ai_lib.init_rknn_model_engine.restype = c_int
        self.ai_lib.init_rknn_model_engine.argtypes = [c_void_p]

        # 3) RknnRet delete_rknn_model_engine(rknn_ai ** rknnEngine); 函数说明
        self.ai_lib.delete_rknn_model_engine.restype = c_int
        self.ai_lib.delete_rknn_model_engine.argtypes = [POINTER(c_void_p)]

        # 4）char* rknn_model_engine_inference(rknn_ai* rknnEngine , uint8_t* imageBuf, uint32_t imageBufSize,
        # char* imageBufType,char* taskID,int w,int h,decode_data_st_t* decode_data) 函数说明
        self.ai_lib.rknn_model_engine_inference.restype = c_char_p
        self.ai_lib.rknn_model_engine_inference.argtypes = [c_void_p, c_char_p, c_uint32, c_char_p, c_char_p, c_int, c_int]

        # 5)  char* rknn_track_model_engine_inference(rknn_ai* rknnEngine , uint8_t* imageBuf, uint32_t imageBufSize,
        #  const char* imageBufType,const char* taskID,int width,int height,const char* detectResult,decode_data_st_t *decode_data=nullptr);
        self.ai_lib.rknn_track_model_engine_inference.restype = c_char_p
        self.ai_lib.rknn_track_model_engine_inference.argtypes = [c_void_p, c_char_p, c_uint32, c_char_p, c_char_p, c_int, c_int, c_char_p]

        # 6)RknnRet rknnx_get_device_info(char* deviceType ,char* filePath);
        self.ai_lib.rknnx_get_device_info.restype = c_int
        self.ai_lib.rknnx_get_device_info.argtypes = [c_char_p, c_char_p]

        # 7)char* rknn_model_engine_inference_batch(rknn_ai* rknnEngine ,decode_data_st_t** decode_data_vec,int batch, const char* imageBufType,const char* taskID);
        self.ai_lib.rknn_model_engine_inference_batch.restype = c_char_p
        self.ai_lib.rknn_model_engine_inference_batch.argtypes = [c_void_p, POINTER(POINTER(DecodeDataRgb)), c_int, c_char_p, c_char_p, c_bool]

        # 识别线程列表
        self.thread_list = []
        self.modelEngPidList = []

    # 创建模型识别引擎
    def create_model_engine(self, modelPath, threshold, modelEngPidList):
        modelEngPid1 = c_void_p(self.ai_lib.creat_rknn_model_engine(modelPath.encode(), threshold))
        ret = self.ai_lib.init_rknn_model_engine(modelEngPid1)
        if ret < 0:
            print("init rknn model error")
        else:
            modelEngPidList.append(modelEngPid1)
            print("modelEngPid1:", modelEngPid1)

    #
    def delete_model_engine(self, modelEngPidList):
        # 3)释放识别引擎 delete_rknn_model_engine 函数
        # 说明
        for engIndex in range(len(modelEngPidList)):
            ret = self.ai_lib.delete_rknn_model_engine(byref(modelEngPidList[engIndex]))
            if ret < 0:
                print("delete rknn model error:,", modelEngPidList[engIndex])
            else:
                print("delete rknn model success:", modelEngPidList[engIndex])

    def create_camera_thread(self, cameraIndex, modelEngPidList):
        my_thread = threading.Thread(target=self.image_detection, args=(cameraIndex, modelEngPidList))
        my_thread.start()
        self.thread_list.append(my_thread)

    def load_image_to_struct(self, img):
        height, width, _ = img.shape

        # Use numpy's ctypes interface to create the buffer directly
        img_flat = img.ravel()  # This flattens the array without copying
        rgb_buf = img_flat.ctypes.data_as(POINTER(c_uint8))

        print("load_image_to_struct len img_flat:", img_flat.size)

        decode_data = DecodeDataRgb()
        decode_data.width = width
        decode_data.height = height
        decode_data.rgb_len = img_flat.size
        decode_data.rgb_buf = rgb_buf
        print("rgb_buf:", rgb_buf)

        return decode_data

    def generate_voc_xml(self, image_filename, image_path, image_data, xml_path, annotations):
        """Generate VOC format XML file"""
        import xml.etree.ElementTree as ET
        
        height, width, _ = image_data.shape
        
        root = ET.Element("annotation")
        
        folder = ET.SubElement(root, "folder")
        folder.text = "images"  # 根据你提供的示例
        
        filename = ET.SubElement(root, "filename")
        filename.text = image_filename
        
        path = ET.SubElement(root, "path")
        path.text = image_path
        
        source = ET.SubElement(root, "source")
        database = ET.SubElement(source, "database")
        database.text = "Unknown"
        
        size = ET.SubElement(root, "size")
        width_elem = ET.SubElement(size, "width")
        width_elem.text = str(width)
        
        height_elem = ET.SubElement(size, "height")
        height_elem.text = str(height)
        
        depth = ET.SubElement(size, "depth")
        depth.text = "3"
        
        segmented = ET.SubElement(root, "segmented")
        segmented.text = "0"
        
        for item in annotations:
            object_elem = ET.SubElement(root, "object")
            
            name = ET.SubElement(object_elem, "name")
            name.text = item["className"]
            
            pose = ET.SubElement(object_elem, "pose")
            pose.text = "Unspecified"
            
            truncated = ET.SubElement(object_elem, "truncated")
            truncated.text = "0"
            
            difficult = ET.SubElement(object_elem, "difficult")
            difficult.text = "0"
            
            bndbox = ET.SubElement(object_elem, "bndbox")
            
            xmin = ET.SubElement(bndbox, "xmin")
            xmin.text = str(round(item["X1"]))
            
            ymin = ET.SubElement(bndbox, "ymin")
            ymin.text = str(round(item["Y1"]))
            
            xmax = ET.SubElement(bndbox, "xmax")
            xmax.text = str(round(item["X2"]))
            
            ymax = ET.SubElement(bndbox, "ymax")
            ymax.text = str(round(item["Y2"]))
            
            # 如果需要的话，可以添加相似度信息
            # similarity = ET.SubElement(object_elem, "similarity")
            # similarity.text = str(item["similarity"])
        
        tree = ET.ElementTree(root)
        tree.write(xml_path)

    def save_cropped_images(self, image, annotations, base_filename, output_folder):
        """根据坐标裁剪小图并保存"""
        for idx, item in enumerate(annotations):
            x1 = round(item["X1"])
            y1 = round(item["Y1"])
            x2 = round(item["X2"])
            y2 = round(item["Y2"])
            
            # 确保坐标在图像范围内
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(image.shape[1], x2)
            y2 = min(image.shape[0], y2)
            
            if x1 >= x2 or y1 >= y2:
                continue  # 跳过无效的坐标
            
            # 裁剪图像
            cropped_img = image[y1:y2, x1:x2]
            
            # 生成文件名: 原文件名_类别_序号_坐标.jpg
            filename = f"{base_filename}_{item['className']}_{idx+1}_{x1}_{y1}_{x2}_{y2}.jpg"
            
            # 保存裁剪的图像
            save_path = os.path.join(output_folder, filename)
            cv2.imwrite(save_path, cropped_img)
            print(f"Saved cropped image: {save_path}")

    def image_detection(self, cameraIndex, modelEngPidList):
        count = 0
        batch_size = 16  # 设置批次大小为 8
        garbage_street_scattered_folder_path = RESULT_SAVE
        files = os.listdir(garbage_street_scattered_folder_path)
        files = [f for f in files if f.lower().endswith(('.jpg', '.jpeg'))]  # 筛选出图片文件

        output_folder = "./output"  # 指定保存结果的文件夹
        os.makedirs(output_folder, exist_ok=True)  # 确保文件夹存在
        xml_output_folder = "./outputxml"  # 指定保存XML的文件夹
        os.makedirs(xml_output_folder, exist_ok=True)  # 确保文件夹存在
        cropped_output_folder = "./cropped_images"  # 指定保存裁剪小图的文件夹
        os.makedirs(cropped_output_folder, exist_ok=True)  # 确保文件夹存在

        # 分批处理图片
        for i in range(0, len(files), batch_size):
            batch_files = files[i:i + batch_size]
            decodeList = []
            matList = []
            for filename in batch_files:
                file_path = os.path.join(garbage_street_scattered_folder_path, filename)
                print("Processing image:", file_path)
                img1 = cv2.imread(file_path)
                image1 = self.load_image_to_struct(img1)
                decodeList.append(image1)
                matList.append(img1)

            # 批量推理
            decode_data_array = (POINTER(DecodeDataRgb) * len(decodeList))(*(pointer(decode) for decode in decodeList))
            print("rknn_model_engine_inference_batch============")
            result = self.ai_lib.rknn_model_engine_inference_batch(
                modelEngPidList[0], decode_data_array, len(decodeList), "MAT".encode(), "task".encode(), c_bool(False))
            if result is not None:
                resultData = json.loads(c_char_p(result).value)
                print('resultData', resultData)
                imageObjs = resultData["imageObjs"]
                for idx, imageObj in enumerate(imageObjs):
                    image = matList[idx]
                    annotations = imageObj["alarmList"]
                    if not annotations:
                        continue
                    
                    # 绘制检测框
                    # for item in annotations:
                    #     newX1 = round(item["X1"])
                    #     newY1 = round(item["Y1"])
                    #     newX2 = round(item["X2"])
                    #     newY2 = round(item["Y2"])
                    #     image = cv2.rectangle(image, (newX1, newY1), (newX2, newY2), (255, 0, 0), 2)
                    #     className = item["className"]
                    #     similarity = round(item["similarity"], 2)
                    #     information = f"{className} {similarity}"
                    #     image = cv2.putText(image, information, (newX1 + 10, newY1), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 0, 0), 2)

                    # 获取原文件名（不包含扩展名）
                    original_filename = os.path.splitext(batch_files[idx])[0]
                    
                    # 指定保存路径，使用原始文件名
                    save_img_path = os.path.join(output_folder, f"{original_filename}_result.jpg")
                    save_xml_path = os.path.join(xml_output_folder, f"{original_filename}.xml")
                    
                    # cv2.imwrite(save_img_path, image)  # 保存图像文件
                    
                    # 生成VOC格式XML文件
                    if annotations:
                        img_path = os.path.join(garbage_street_scattered_folder_path, batch_files[idx])
                        self.generate_voc_xml(batch_files[idx], img_path, image, save_xml_path, annotations)
                        # 保存裁剪的小图
                        self.save_cropped_images(image, annotations, original_filename, cropped_output_folder)
                    
            count += 1
            time.sleep(3)
            if count == 1000:
                break


# ---------------------------------------------------------------------------------------
def evaluate():
    """基于边缘推理结果生成评估报告, 与云端报告格式一致."""
    os.makedirs(EVAL_SAVE_DIR, exist_ok=True)
    predictions_path = os.path.join(EVAL_SAVE_DIR, "predictions.jsonl")
    predictions_file = open(predictions_path, "w", encoding="utf-8") if DUMP_PREDICTIONS else None

    dataset_class_names, samples = load_dataset_from_yaml(EVAL_DATA_PATH)
    class_names = resolve_class_names(dataset_class_names)
    class_count = len(class_names)
    class_name_to_id = {name: i for i, name in enumerate(class_names)}
    print("class_names:", class_names)

    if EVAL_XML_DIR and os.path.isdir(EVAL_XML_DIR):
        attach_scene_attrs(samples, EVAL_XML_DIR)

    ai_clib = AILib()
    print(f"AI lib: {ai_clib.ai_lib_path}")
    ai_clib.create_model_engine(MODEL_PATH, c_float(EVAL_CONF_THRES), ai_clib.modelEngPidList)

    global_per_class = {cid: _empty_stats() for cid in range(class_count)}
    scene_accum: dict = {}
    size_accum = {b[0]: {cid: _empty_stats() for cid in range(class_count)} for b in SIZE_BINS}
    label_counts = np.zeros(class_count, dtype=int)
    inference_seconds = 0.0
    image_details = []

    start_time = time.perf_counter()

    for sample in samples:
        image = cv2.imread(sample.image_path)
        if image is None:
            continue
        img_h, img_w = image.shape[:2]

        gt_boxes = load_yolo_labels(
            sample.label_path,
            img_w,
            img_h,
            class_names=class_names,
            xml_label_path=getattr(sample, "xml_label_path", None),
        )
        for gt in gt_boxes:
            if 0 <= gt.class_id < class_count:
                label_counts[gt.class_id] += 1

        # 边缘推理 (C库期望RGB格式)
        input_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB) if EVAL_INPUT_COLOR == "rgb" else image
        decode_data = ai_clib.load_image_to_struct(input_image)
        decode_array = (POINTER(DecodeDataRgb) * 1)(pointer(decode_data))
        t0 = time.perf_counter()
        result = ai_clib.ai_lib.rknn_model_engine_inference_batch(
            ai_clib.modelEngPidList[0], decode_array, 1, "MAT".encode(), "eval".encode(), c_bool(False)
        )
        inference_seconds += time.perf_counter() - t0

        predictions = []
        if result is not None:
            result_data = json.loads(c_char_p(result).value)
            alarm_list = result_data.get("imageObjs", [{}])[0].get("alarmList", [])
            predictions = parse_alarm_predictions(alarm_list, class_name_to_id, img_w, img_h)
        if predictions_file:
            predictions_file.write(
                json.dumps(
                    prediction_record(sample, predictions, class_names, img_w, img_h),
                    ensure_ascii=False,
                ) + "\n"
            )

        grouped = match_detections_grouped(
            predictions, gt_boxes, class_count,
            iou_threshold=EVAL_IOU_THRES,
            scene_attrs=sample.scene_attrs if sample.scene_attrs else None,
            img_w=img_w, img_h=img_h,
        )
        image_details.append(
            image_match_detail(sample, predictions, gt_boxes, class_names, class_count, EVAL_IOU_THRES)
        )

        for cid in range(class_count):
            for k in ('tp', 'fp', 'fn'):
                global_per_class[cid][k] += grouped['per_class'][cid][k]
            global_per_class[cid]['scores'].extend(grouped['per_class'][cid]['scores'])
            global_per_class[cid]['matches'].extend(grouped['per_class'][cid]['matches'])

        for field_key, val_dict in grouped['scene'].items():
            if field_key not in scene_accum:
                scene_accum[field_key] = {}
            for val, pc in val_dict.items():
                if val not in scene_accum[field_key]:
                    scene_accum[field_key][val] = {cid: _empty_stats() for cid in range(class_count)}
                for cid in range(class_count):
                    for k in ('tp', 'fp', 'fn'):
                        scene_accum[field_key][val][cid][k] += pc[cid][k]
                    scene_accum[field_key][val][cid]['scores'].extend(pc[cid]['scores'])
                    scene_accum[field_key][val][cid]['matches'].extend(pc[cid]['matches'])

        for bin_name, pc in grouped['size'].items():
            for cid in range(class_count):
                for k in ('tp', 'fp', 'fn'):
                    size_accum[bin_name][cid][k] += pc[cid][k]
                size_accum[bin_name][cid]['scores'].extend(pc[cid]['scores'])
                size_accum[bin_name][cid]['matches'].extend(pc[cid]['matches'])

    ai_clib.delete_model_engine(ai_clib.modelEngPidList)

    total_time = time.perf_counter() - start_time
    cpu_mb = get_cpu_memory_mb()

    summary = summarize_metrics(
        class_names=class_names,
        per_class=global_per_class,
        label_counts=label_counts,
        total_images=len(samples),
        total_time_seconds=total_time,
        cpu_memory_mb=cpu_mb,
    )
    summary['extra_speed_metrics'] = {
        '平均推理耗时(ms/图)': (inference_seconds / max(len(samples), 1)) * 1000,
    }

    scene_summary = summarize_scene_metrics(scene_accum, class_names) if scene_accum else {}
    size_summary  = summarize_size_metrics(size_accum, class_names)

    report_path = write_txt_report(
        EVAL_SAVE_DIR, summary,
        thresholds={
            "model_path": MODEL_PATH,
            "ai_lib": ai_clib.ai_lib_path,
            "dataset": EVAL_DATA_PATH,
            "confidence_threshold": EVAL_CONF_THRES,
            "nms_threshold": BMODEL_NMS_THRES,
            "match_iou_threshold": EVAL_IOU_THRES,
            "backend": "bmodel C API",
        },
        scene_summary=scene_summary,
        size_summary=size_summary,
    )

    image_detail_path = write_image_details(EVAL_SAVE_DIR, image_details, class_names)
    append_image_summary(report_path, image_details, image_detail_path)
    print(f"image details: {image_detail_path}")

    print(f"图片: {len(samples)} | mAP50: {summary['map50']:.4f}")
    print(f"报告: {report_path}")

    if predictions_file:
        predictions_file.close()
        print(f"predictions: {predictions_path}")

# ---------------------------------------------------------------------------------------
if __name__ == '__main__':
    evaluate()
