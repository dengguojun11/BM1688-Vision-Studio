// @ts-ignore
if (typeof (window as any).global === 'undefined') {
  (window as any).global = window;
}

/** 开发环境过滤第三方库已知弃用告警，避免控制台刷屏 */
if (import.meta.env.DEV) {
  const suppressPatterns = [
    'forwardRef render functions accept exactly two parameters',
    'findDOMNode is deprecated',
    'componentWillMount has been renamed',
    'React Router Future Flag Warning',
  ];
  const wrap = (fn: typeof console.error) => {
    return (...args: unknown[]) => {
      const first = args[0];
      const text = typeof first === 'string' ? first : String(first ?? '');
      if (suppressPatterns.some((p) => text.includes(p))) {
        return;
      }
      fn.apply(console, args as Parameters<typeof console.error>);
    };
  };
  console.error = wrap(console.error);
  console.warn = wrap(console.warn);
}
