import RcTooltip from 'rc-tooltip';
import type { TooltipProps } from 'rc-tooltip/lib/Tooltip';

import GlobalStyle, { tooltipPrefix } from './GlobalStyle';

/**
 * Render the tooltip stylesheet once at the application root.  Rendering it
 * inside every Tooltip makes the 800+ global rules unmount whenever a tooltip
 * unmounts (e.g. while navigating between samples) and forces styled-components
 * to rebuild the whole rule group, which blocks the main thread for seconds.
 */
export const TooltipGlobalStyle = GlobalStyle;

export function Tooltip(props: TooltipProps) {
  return <RcTooltip prefixCls={tooltipPrefix} {...props} />;
}
