import { useState } from 'react';
import type { RcFile } from 'antd/lib/upload/interface';
import { UploadOutlined } from '@ant-design/icons';

import NativeUpload from '@/components/NativeUpload';
import { uploadPreAnnotationFile } from '@/api/services/preAnnotations';
import { message } from '@/StaticAnt';

import { selectCurrentImagePreAnnotationFile } from '../../samplePreAnnotationUpload';

interface SamplePreAnnotationUploadProps {
  taskId: number;
  sampleFilename: string;
  disabled?: boolean;
  onCompleted?: () => void | Promise<void>;
}

export default function SamplePreAnnotationUpload({
  taskId,
  sampleFilename,
  disabled = false,
  onCompleted,
}: SamplePreAnnotationUploadProps) {
  const [uploading, setUploading] = useState(false);

  const handleChange = async (files: RcFile[]) => {
    const file = files[0];
    if (!file || uploading) return;
    setUploading(true);
    try {
      const selected = await selectCurrentImagePreAnnotationFile(file, sampleFilename);
      await uploadPreAnnotationFile(taskId, selected);
      await onCompleted?.();
      message.success('当前图片预标注已上传');
    } catch (error: any) {
      message.error(error?.response?.data?.msg || error?.message || '预标注文件上传失败');
    } finally {
      setUploading(false);
    }
  };

  return (
    <NativeUpload
      type="text"
      icon={<UploadOutlined />}
      accept=".json,.jsonl,application/json,application/jsonl"
      multiple={false}
      disabled={disabled || uploading}
      onChange={(files) => void handleChange(files)}
      data-labelu-preannotation-upload="1"
    >
      {uploading ? '上传中...' : '上传预标注'}
    </NativeUpload>
  );
}
