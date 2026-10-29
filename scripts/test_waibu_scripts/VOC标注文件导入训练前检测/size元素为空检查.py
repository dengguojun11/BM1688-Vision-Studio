import os
from PIL import Image
import xml.etree.ElementTree as ET

def add_size_element_if_missing(xml_file_path, image_folder_path):
    # 尝试从XML文件中获取图片的实际尺寸
    try:
        tree = ET.parse(xml_file_path)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"Error parsing XML file {xml_file_path}: {e}")
        return

    # 检查<size>元素是否存在
    size_elem = root.find('size')
    if size_elem is None:
        # 获取图片文件名
        filename_elem = root.find('filename')
        if filename_elem is not None:
            image_filename = filename_elem.text
            image_path = os.path.join(image_folder_path, image_filename)

            # 尝试读取图片的实际尺寸
            try:
                with open(image_path, 'rb') as image_file:
                    img = Image.open(image_file)
                    width, height = img.size
                    depth = len(img.getbands())  # 通常对于RGB图像，depth是3

                    # 添加<size>元素
                    size_elem = ET.SubElement(root, 'size')
                    ET.SubElement(size_elem, 'width').text = str(width)
                    ET.SubElement(size_elem, 'height').text = str(height)
                    ET.SubElement(size_elem, 'depth').text = str(depth)

                    # 保存更新后的XML文件
                    tree.write(xml_file_path, encoding='utf-8', xml_declaration=True)
                    print(f"Added <size> element to {xml_file_path}")
            except IOError as e:
                print(f"Error reading image file {image_path}: {e}")
    else:
        print(f"<size> element already exists in {xml_file_path}")

# 定义文件夹路径
# xml_folder_path = r'D:\7g\zijinghua\xrjcLjbl0822\12yuexingren\winderperson\labels'
# image_folder_path = r'D:\7g\zijinghua\xrjcLjbl0822\12yuexingren\winderperson\images'
xml_folder_path = r'D:\7g\2502\poop\zenqiang\labels'
image_folder_path = r'D:\7g\2502\poop\zenqiang\images'

# 遍历文件夹中的所有XML文件
for filename in os.listdir(xml_folder_path):
    if filename.endswith('.xml'):
        xml_file_path = os.path.join(xml_folder_path, filename)
        add_size_element_if_missing(xml_file_path, image_folder_path)