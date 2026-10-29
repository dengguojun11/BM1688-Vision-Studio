const imageCache = new Map<string, Promise<HTMLImageElement>>();
const decodedImageCache = new Map<string, HTMLImageElement>();
const preloadedBlobCache = new Map<string, Blob>();
const preloadCache = new Set<string>();
const preloadRequests = new Map<string, Promise<Blob | undefined>>();
/** 标注翻页：缓存更多相邻展示图，减少 175 生产连点「下一张」时的重复下载/解码 */
const DECODED_IMAGE_CACHE_LIMIT = 12;
const PRELOADED_BLOB_CACHE_LIMIT = 12;
const PRELOAD_CACHE_LIMIT = 200;

function trimPreloadCache() {
  while (preloadCache.size > PRELOAD_CACHE_LIMIT) {
    const oldestKey = preloadCache.keys().next().value;

    if (!oldestKey) {
      return;
    }

    preloadCache.delete(oldestKey);
  }
}

function cacheDecodedImage(url: string, image: HTMLImageElement) {
  decodedImageCache.delete(url);
  decodedImageCache.set(url, image);

  while (decodedImageCache.size > DECODED_IMAGE_CACHE_LIMIT) {
    const oldestKey = decodedImageCache.keys().next().value;

    if (!oldestKey) {
      return;
    }

    decodedImageCache.delete(oldestKey);
  }
}

function cachePreloadedBlob(url: string, blob: Blob) {
  preloadedBlobCache.delete(url);
  preloadedBlobCache.set(url, blob);

  while (preloadedBlobCache.size > PRELOADED_BLOB_CACHE_LIMIT) {
    const oldestKey = preloadedBlobCache.keys().next().value;

    if (!oldestKey) {
      return;
    }

    preloadedBlobCache.delete(oldestKey);
  }
}

function loadImageElement(src: string, priority?: 'low' | 'high' | 'auto') {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.decoding = 'async';
    // @ts-ignore
    if (priority && 'fetchPriority' in image) {
      // @ts-ignore
      image.fetchPriority = priority;
    }

    image.onload = () => {
      image.onload = null;
      image.onerror = null;

      const decodeTask = typeof image.decode === 'function' ? image.decode().catch(() => undefined) : Promise.resolve();

      decodeTask.then(() => {
        resolve(image);
      });
    };
    image.onerror = (err) => {
      image.onload = null;
      image.onerror = null;
      reject(err);
    };
    image.src = src;
  });
}

function createObjectUrl(blob: Blob) {
  if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') {
    return null;
  }

  return URL.createObjectURL(blob);
}

function revokeObjectUrl(objectUrl: string | null) {
  if (objectUrl && typeof URL !== 'undefined' && typeof URL.revokeObjectURL === 'function') {
    URL.revokeObjectURL(objectUrl);
  }
}

const LOW_PRIORITY_FETCH_INIT = { priority: 'low' } as RequestInit;

export function loadCachedImage(url: string, priority?: 'low' | 'high' | 'auto'): Promise<HTMLImageElement> {
  const decoded = decodedImageCache.get(url);

  if (decoded) {
    cacheDecodedImage(url, decoded);
    return Promise.resolve(decoded);
  }

  const cached = imageCache.get(url);

  if (cached) {
    return cached;
  }

  const createImageLoad = async (blob?: Blob) => {
    const cachedBlob = blob ?? preloadedBlobCache.get(url);
    const objectUrl = cachedBlob ? createObjectUrl(cachedBlob) : null;

    try {
      const image = await loadImageElement(objectUrl ?? url, priority);
      cacheDecodedImage(url, image);
      return image;
    } catch (err) {
      imageCache.delete(url);
      throw err;
    } finally {
      revokeObjectUrl(objectUrl);
    }
  };

  const pendingPreload = preloadRequests.get(url);
  const promise =
    pendingPreload ? pendingPreload.then(createImageLoad, () => createImageLoad()) : createImageLoad();

  imageCache.set(url, promise);
  promise.then(
    () => {
      imageCache.delete(url);
    },
    () => {
      imageCache.delete(url);
    },
  );

  return promise;
}

export function decodeImage(url?: string | null, priority: 'low' | 'high' | 'auto' = 'low') {
  if (!url || typeof window === 'undefined') {
    return Promise.resolve(undefined);
  }

  return loadCachedImage(url, priority).then(() => undefined);
}

export function preloadImage(url?: string | null) {
  if (
    !url ||
    typeof window === 'undefined' ||
    preloadCache.has(url) ||
    imageCache.has(url) ||
    decodedImageCache.has(url)
  ) {
    return;
  }

  preloadCache.add(url);
  trimPreloadCache();

  // 使用 fetch 预下载图片字节，不提前解码，避免把未显示的图片解码成庞大的 Bitmap 占用内存
  const request = fetch(url, LOW_PRIORITY_FETCH_INIT)
    .then((response) => {
      if (!response.ok) {
        preloadCache.delete(url);
        return undefined;
      }

      return response.blob();
    })
    .then((blob) => {
      if (blob) {
        cachePreloadedBlob(url, blob);
      }

      return blob;
    })
    .catch(() => {
      preloadCache.delete(url);
      return undefined;
    })
    .finally(() => {
      preloadRequests.delete(url);
    });

  preloadRequests.set(url, request);
}

export function clearImageCache() {
  imageCache.clear();
  decodedImageCache.clear();
  preloadedBlobCache.clear();
  preloadCache.clear();
  preloadRequests.clear();
}
