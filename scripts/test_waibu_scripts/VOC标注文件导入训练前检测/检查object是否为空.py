import os
import xml.etree.ElementTree as ET

# 指定 XML 文件夹路径
# xml_folder_path = r'D:\7g\zijinghua\xrjcLjbl0822\12yuexingren\winderperson\labels'
xml_folder_path = r'D:\7g\2511\ljbl\labels'

# 遍历文件夹中的所有 XML 文件
for filename in os.listdir(xml_folder_path):
    if filename.endswith('.xml'):
        # 构建 XML 文件的完整路径
        file_path = os.path.join(xml_folder_path, filename)
        
        # 解析 XML 文件
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        # 检查是否存在 <object> 元素
        objects = root.findall('object')
        if not objects:
            # 如果没有找到 <object> 元素，则删除该 XML 文件
            os.remove(file_path)
            print(f"Deleted {filename} because it contains no <object> elements")

print("完成 XML 文件检查和清理。")