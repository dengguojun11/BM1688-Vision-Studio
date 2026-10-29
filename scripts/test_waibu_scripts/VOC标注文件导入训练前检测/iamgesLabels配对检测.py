import os

# 指定图片文件夹和XML文件夹路径
# images_folder_path = r'D:\7g\zijinghua\xrjcLjbl0822\12yuexingren\winderperson\images'
# xml_folder_path = r'D:\7g\zijinghua\xrjcLjbl0822\12yuexingren\winderperson\labels'
images_folder_path = r'E:\7G\AI\roadsideillegalvending\data\images'
xml_folder_path = r'E:\7G\AI\roadsideillegalvending\data\labels'

# 获取两个文件夹中所有文件的列表
images_files = set([os.path.splitext(f)[0] for f in os.listdir(images_folder_path) if f.endswith(('.jpg'))])
xml_files = set([os.path.splitext(f)[0] for f in os.listdir(xml_folder_path) if f.endswith('.xml')])

# 找出不符合条件的文件：在图片文件夹中但不在XML文件夹中，或在XML文件夹中但不在图片文件夹中
unmatched_images = images_files - xml_files
unmatched_xmls = xml_files - images_files

# 删除不符合条件的图片文件
for img in unmatched_images:
    os.remove(os.path.join(images_folder_path, img + '.jpg'))  # 假设图片文件是.jpg格式
    print(f"Deleted unmatched image file: {img}.jpg")

# 删除不符合条件的XML文件
for xml in unmatched_xmls:
    os.remove(os.path.join(xml_folder_path, xml + '.xml'))
    print(f"Deleted unmatched xml file: {xml}.xml")

# 验证最终的配对情况
final_images_files = set([os.path.splitext(f)[0] for f in os.listdir(images_folder_path) if f.endswith(('.jpg', '.jpeg', '.png'))])
final_xml_files = set([os.path.splitext(f)[0] for f in os.listdir(xml_folder_path) if f.endswith('.xml')])

# 确保两个文件夹的文件数量一致
assert len(final_images_files) == len(final_xml_files), "The number of images and xml files is not consistent."

print("All images and xml files are correctly paired, and the count is consistent.")