import { EInternalEvent } from '../enums';
import { axis, eventEmitter } from '../singletons';
import type { AllShape } from '../shapes/types';

interface DomPortalPosition {
  x: number;
  y: number;
  rotate?: number;
}

export interface DomPortalParams {
  rotate?: number;
  order?: number;
  getPosition?: (shape: AllShape, wrapper: HTMLElement) => DomPortalPosition;
  content: HTMLElement | string;
  bindShape: AllShape;
  preventPointerEvents?: boolean;
  style?: Record<string, string>;
}

export class DomPortal {
  public x: number = 0;

  public y: number = 0;

  public order: number = 2;

  /**
   * html string or dom element
   */
  private _content: string | HTMLElement | null = null;

  private _rotate: number = 0;

  private _preventPointerEvents: boolean = false;

  private _container: HTMLElement = axis!.renderer!.canvas.parentElement!;

  private _wrapper: HTMLElement = document.createElement('div');

  private _shape: AllShape;

  private _getPosition: () => DomPortalPosition;

  private _removeShapeListener?: () => void;

  constructor({
    content,
    bindShape,
    preventPointerEvents = false,
    order = 2,
    rotate = 0,
    getPosition,
    style,
  }: DomPortalParams) {
    this._content = content;
    this._shape = bindShape;
    this._preventPointerEvents = preventPointerEvents;
    this.order = order;
    this._rotate = rotate;
    this._getPosition = () => {
      let position: DomPortalPosition = {
        x: 0,
        y: 0,
      };

      if (typeof getPosition === 'function') {
        position = getPosition(this._shape, this._wrapper);
      } else {
        position = {
          x: this._shape.dynamicCoordinate[0].x,
          y: this._shape.dynamicCoordinate[0].y,
        };
      }

      this.x = position.x;
      this.y = position.y;

      if (position.rotate) {
        this._rotate = position.rotate;
      }

      return position;
    };

    if (bindShape) {
      eventEmitter.on(EInternalEvent.AxisChange, this._handleUpdatePosition);
      if (typeof bindShape.onCoordinateChange === 'function') {
        this._removeShapeListener = bindShape.onCoordinateChange(this._handleUpdatePosition);
      }
    }

    if (!content) {
      throw new Error('Element must be set');
    }

    if (this._container.contains(this._wrapper)) {
      console.warn('Container already contains the element');
    }

    if (typeof this._content === 'string') {
      this._wrapper.innerHTML = this._content;
    } else {
      this._wrapper.appendChild(this._content);
    }

    if (style) {
      Object.assign(this._wrapper.style, style);
    }

    this._container.appendChild(this._wrapper);
    
    // 延迟初始样式的计算（尤其是读取 clientHeight），打破循环创建时的 Layout Thrashing (强制同步回流)
    this._wrapper.style.position = 'absolute';
    this._wrapper.style.left = '0';
    this._wrapper.style.top = '0';
    this._wrapper.style.userSelect = 'none';
    this._wrapper.style.display = 'block';
    this._wrapper.style.transformOrigin = 'center center';
    this._wrapper.style.zIndex = `${this.order}`;
    this._wrapper.style.pointerEvents = this._preventPointerEvents ? 'none' : 'auto';

    // 触发首次位置更新
    this._handleUpdatePosition();
  }

  private _rafId: number | null = null;

  private _handleUpdatePosition = () => {
    if (this._rafId) return;
    this._rafId = requestAnimationFrame(() => {
      this._rafId = null;
      if (!this._container.contains(this._wrapper)) return;
      this._updatePosition();
    });
  };

  private _updatePosition() {
    const { _wrapper } = this;

    const position = this._getPosition();

    _wrapper.style.transform = `translate(${position.x}px, ${position.y}px) rotate(${this._rotate}deg)`;
  }

  public set rotate(rotate: number) {
    this._rotate = rotate;
    this._wrapper.style.transform = `translate(${this.x}px, ${this.y}px) rotate(${rotate}deg)`;
  }

  public get rotate() {
    return this._rotate;
  }

  public show() {
    this._wrapper.style.display = 'block';
  }

  public hide() {
    this._wrapper.style.display = 'none';
  }

  public toTop() {
    this._wrapper.style.zIndex = '1049';
  }

  public resetZIndex() {
    this._wrapper.style.zIndex = `${this.order}`;
  }

  public destroy() {
    this._wrapper.remove();
    eventEmitter.off(EInternalEvent.AxisChange, this._handleUpdatePosition);
    if (this._removeShapeListener) {
      this._removeShapeListener();
      this._removeShapeListener = undefined;
    }
  }
}
