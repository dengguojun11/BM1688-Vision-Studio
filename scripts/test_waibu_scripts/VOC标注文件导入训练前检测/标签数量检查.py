import os
import xml.etree.ElementTree as ET
from collections import Counter

# 指定包含XML文件的文件夹路径
# xml_folder_path = r'D:\7g\zijinghua\xrjcLjbl0822\12yuexingren\bicycleNew\labels'
xml_folder_path = r'D:\7g\2510\labels'

# 用于存储name出现次数的计数器
name_counter = Counter()

# 遍历文件夹中的所有文件
for filename in os.listdir(xml_folder_path):
    if filename.endswith('.xml'):
        file_path = os.path.join(xml_folder_path, filename)
        
        # 解析XML文件
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        # 遍历所有的object元素
        for obj in root.findall('object'):
            # 获取name标签的文本内容
            name = obj.find('name').text
            # 更新计数器
            name_counter[name] += 1

# 打印每个name的出现次数
for name, count in name_counter.items():
    print(f"{name}: {count}")