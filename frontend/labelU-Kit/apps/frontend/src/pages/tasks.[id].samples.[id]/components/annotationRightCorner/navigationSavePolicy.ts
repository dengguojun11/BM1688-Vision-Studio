import { SampleState } from '@/api/types';

/**
 * 判断切换样本前是否需要保存画布。
 *
 * SKIPPED 是已经持久化的样本状态，切换时不应再次读取画布并保存；
 * 否则保存函数会按设计返回 false，导航会被误判为保存失败而中止。
 */
export function shouldSaveBeforeSampleNavigation(options: {
  sampleState?: SampleState;
  noSave?: boolean;
  isCompareAccessMode?: boolean;
  /** 只有当前用户持有编辑权时才读取画布并持久化。 */
  isCurrentUser?: boolean;
}): boolean {
  return (
    !options.noSave &&
    !options.isCompareAccessMode &&
    options.sampleState !== SampleState.SKIPPED &&
    options.isCurrentUser === true
  );
}
