import os
import xml.etree.ElementTree as ET

def check_and_update_filenames(directory):
    for filename in os.listdir(directory):
        if filename.endswith('.xml'):
            xml_path = os.path.join(directory, filename)
            try:
                tree = ET.parse(xml_path)
                root = tree.getroot()
                file_tag = root.find('filename')
                
                if file_tag is not None:
                    expected_filename = filename[:-4] + '.jpg'
                    actual_filename = file_tag.text
                    
                    # 如果文件名不一致，进行修改
                    if actual_filename != expected_filename:
                        print(f'文件名不一致: {filename}')
                        file_tag.text = expected_filename  
                        tree.write(xml_path)  
                        print(f'已修改为: {expected_filename}')
                    else:
                        print(f'文件名匹配: {filename}')
                else:
                    print(f'XML文件 {filename} 中没有找到<filename>标签')
            except ET.ParseError as e:
                print(f'解析错误：无法解析XML文件 {filename}，错误信息：{e}')
            except Exception as e:
                print(f'处理文件 {filename} 时发生未知错误：{e}')

# 指定要遍历的文件夹路径
# directory_path = r'D:\7g\zijinghua\xrjcLjbl0822\12yuexingren\winderperson\labels'  
directory_path = r'D:\7g\2602\labels' 
check_and_update_filenames(directory_path)