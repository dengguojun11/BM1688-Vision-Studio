interface GlobalAttributeConfig {
  value: string;
  required?: boolean;
}

interface GlobalAnnotationValue {
  value?: Record<string, unknown>;
}

export function isGlobalToolCompleted(
  tagAttributes: GlobalAttributeConfig[],
  textAttributes: GlobalAttributeConfig[],
  annotations: Record<string, GlobalAnnotationValue>,
) {
  const completionAttributes = [...textAttributes.filter((item) => item.required), ...tagAttributes];

  return (
    completionAttributes.length > 0 &&
    completionAttributes.every((item) => Boolean(annotations[item.value]?.value?.[item.value]))
  );
}
