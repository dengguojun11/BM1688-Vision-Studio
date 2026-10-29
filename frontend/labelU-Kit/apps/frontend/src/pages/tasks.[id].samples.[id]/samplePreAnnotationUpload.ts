import { Draft07 } from 'json-schema-library';

import imageJsonSchema from '../tasks.[id].edit/partials/InputData/imagePreAnnotationJson.schema.json';
import imageJsonlSchema from '../tasks.[id].edit/partials/InputData/imagePreAnnotationJsonl.schema.json';
import { readFile } from '../tasks.[id].edit/partials/InputData/utils';

type JsonRecord = Record<string, unknown>;

const normalizeFilename = (value: unknown) => String(value ?? '').replaceAll('\\', '/').split('/').pop() || '';

function validateRecord(value: unknown, schema: object) {
  const errors = new Draft07(schema).validate(value);
  if (errors.length) {
    throw new Error(errors.map((error) => error.message).join('; '));
  }
}

/**
 * Keep only records for the current image, then return a task-edit-compatible
 * JSON or JSONL file. The server still performs its own validation when the
 * attachment is registered.
 */
export async function selectCurrentImagePreAnnotationFile(file: File, sampleFilename: string): Promise<File> {
  const lowerName = file.name.toLowerCase();
  if (!lowerName.endsWith('.json') && !lowerName.endsWith('.jsonl')) {
    throw new Error('仅支持 .json 或 .jsonl 预标注文件');
  }
  const content = await readFile(file, 'text');
  const currentName = normalizeFilename(sampleFilename);
  if (lowerName.endsWith('.jsonl')) {
    const records = content
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => JSON.parse(line) as JsonRecord);
    records.forEach((record) => validateRecord(record, imageJsonlSchema));
    const selected = records.filter((record) => normalizeFilename(record.sample_name) === currentName);
    if (!selected.length) {
      throw new Error(`JSONL 中未找到当前图片：${sampleFilename}`);
    }
    return new File([selected.map((record) => JSON.stringify(record)).join('\n') + '\n'], file.name, {
      type: 'application/jsonl',
    });
  }

  const parsed = JSON.parse(content);
  if (!Array.isArray(parsed)) {
    throw new Error('JSON 预标注文件必须是数组');
  }
  parsed.forEach((record) => {
    if (!record || typeof record !== 'object' || typeof (record as JsonRecord).result !== 'string') {
      throw new Error('JSON 预标注记录的 result 必须是 JSON 字符串');
    }
    const result = JSON.parse((record as JsonRecord).result as string);
    validateRecord({ ...(record as JsonRecord), result }, imageJsonSchema);
  });
  const selected = parsed.filter(
    (record) => normalizeFilename((record as JsonRecord).fileName) === currentName,
  );
  if (!selected.length) {
    throw new Error(`JSON 中未找到当前图片：${sampleFilename}`);
  }
  return new File([JSON.stringify(selected)], file.name, { type: 'application/json' });
}
