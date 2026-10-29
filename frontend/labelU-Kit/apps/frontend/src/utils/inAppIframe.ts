/** 当前页面是否运行在父页面 iframe 内（用于隐藏重复顶栏/Tab）。 */
export function isInAppIframe(): boolean {
  try {
    return window.self !== window.top;
  } catch {
    return true;
  }
}
