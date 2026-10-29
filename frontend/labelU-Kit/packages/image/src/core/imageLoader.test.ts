import { describe, expect, it, vi } from 'vitest';

import { clearImageCache, decodeImage, loadCachedImage, preloadImage } from './imageLoader';

type MockImage = {
  onload?: () => void;
  onerror?: (err: unknown) => void;
  decode?: () => Promise<void>;
  src?: string;
  decoding?: string;
  fetchPriority?: string;
};

function stubFetch() {
  const fetchMock = vi.fn(() => Promise.resolve(new Response(null)));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });

  return { promise, resolve, reject };
}

describe('imageLoader', () => {
  it('deduplicates concurrent loads of the same url without retaining decoded images', async () => {
    clearImageCache();

    const createdImages: MockImage[] = [];

    vi.stubGlobal(
      'Image',
      vi.fn(function () {
        const image: MockImage = {};
        createdImages.push(image);
        return image;
      }),
    );

    try {
      const first = loadCachedImage('/sample-a.jpg');
      const second = loadCachedImage('/sample-a.jpg');

      expect(createdImages).toHaveLength(1);

      createdImages[0].onload?.();

      await expect(first).resolves.toBe(createdImages[0]);
      await expect(second).resolves.toBe(createdImages[0]);

      await Promise.resolve();

      const third = loadCachedImage('/sample-a.jpg');
      expect(createdImages).toHaveLength(2);
      createdImages[1].onload?.();
      await expect(third).resolves.toBe(createdImages[1]);
    } finally {
      vi.unstubAllGlobals();
      clearImageCache();
    }
  });

  it('deduplicates repeated preloads of the same url', () => {
    clearImageCache();
    const fetchMock = stubFetch();

    try {
      preloadImage('/sample-b.jpg');
      preloadImage('/sample-b.jpg');

      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock).toHaveBeenCalledWith('/sample-b.jpg', { priority: 'low' });
    } finally {
      vi.unstubAllGlobals();
      clearImageCache();
    }
  });

  it('retries failed preloads', async () => {
    clearImageCache();
    const fetchMock = vi.fn(() => Promise.reject(new Error('test')));
    vi.stubGlobal('fetch', fetchMock);

    try {
      preloadImage('/sample-c.jpg');
      await Promise.resolve();
      preloadImage('/sample-c.jpg');

      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally {
      vi.unstubAllGlobals();
      clearImageCache();
    }
  });

  it('waits for an in-flight preload before decoding the same url', async () => {
    clearImageCache();

    const fetchTask = deferred<Response>();
    const fetchMock = vi.fn(() => fetchTask.promise);
    const createObjectURL = vi.fn(() => 'blob:/sample-inflight');
    const revokeObjectURL = vi.fn();
    const createdImages: MockImage[] = [];

    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL });
    vi.stubGlobal(
      'Image',
      vi.fn(function () {
        const image: MockImage = {};
        createdImages.push(image);
        return image;
      }),
    );

    try {
      preloadImage('/sample-inflight.jpg');
      const imagePromise = loadCachedImage('/sample-inflight.jpg');

      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(createdImages).toHaveLength(0);

      fetchTask.resolve(new Response(new Blob(['image-bytes'])));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();

      expect(createdImages).toHaveLength(1);
      expect(createdImages[0].src).toBe('blob:/sample-inflight');
      createdImages[0].onload?.();
      await expect(imagePromise).resolves.toBe(createdImages[0]);
      expect(createObjectURL).toHaveBeenCalledTimes(1);
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:/sample-inflight');
    } finally {
      vi.unstubAllGlobals();
      clearImageCache();
    }
  });

  it('reuses an in-flight preload when loading the same url with high priority', async () => {
    clearImageCache();

    const fetchTask = deferred<Response>();
    const fetchMock = vi.fn(() => fetchTask.promise);
    const createObjectURL = vi.fn(() => 'blob:/sample-current');
    const revokeObjectURL = vi.fn();
    const createdImages: MockImage[] = [];

    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL });
    vi.stubGlobal(
      'Image',
      vi.fn(function () {
        const image: MockImage = {};
        createdImages.push(image);
        return image;
      }),
    );

    try {
      preloadImage('/sample-current.jpg');
      const imagePromise = loadCachedImage('/sample-current.jpg', 'high');

      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(createdImages).toHaveLength(0);

      fetchTask.resolve(new Response(new Blob(['image-bytes'])));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();

      expect(createdImages).toHaveLength(1);
      expect(createdImages[0].src).toBe('blob:/sample-current');
      createdImages[0].onload?.();
      await expect(imagePromise).resolves.toBe(createdImages[0]);
      expect(createObjectURL).toHaveBeenCalledTimes(1);
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:/sample-current');
    } finally {
      vi.unstubAllGlobals();
      clearImageCache();
    }
  });

  it('does not start a preload when the image is already loading', () => {
    clearImageCache();
    const fetchMock = stubFetch();

    const createdImages: MockImage[] = [];
    vi.stubGlobal(
      'Image',
      vi.fn(function () {
        const image: MockImage = {};
        createdImages.push(image);
        return image;
      }),
    );

    try {
      loadCachedImage('/sample-loading.jpg');
      preloadImage('/sample-loading.jpg');

      expect(createdImages).toHaveLength(1);
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
      clearImageCache();
    }
  });

  it('evicts rejected URLs', async () => {
    clearImageCache();

    const createdImages: MockImage[] = [];

    vi.stubGlobal(
      'Image',
      vi.fn(function () {
        const image: MockImage = {};
        createdImages.push(image);
        return image;
      }),
    );

    try {
      const promise = loadCachedImage('/sample-c.jpg');

      expect(createdImages).toHaveLength(1);

      createdImages[0].onerror?.(new Error('test'));

      await expect(promise).rejects.toThrow('test');

      const secondPromise = loadCachedImage('/sample-c.jpg');
      expect(createdImages).toHaveLength(2);

      createdImages[1].onload?.();
      await secondPromise;
    } finally {
      vi.unstubAllGlobals();
      clearImageCache();
    }
  });

  it('reuses a small decoded image cache after loads settle', async () => {
    clearImageCache();

    const createdImages: MockImage[] = [];
    vi.stubGlobal(
      'Image',
      vi.fn(function () {
        const image: MockImage = {};
        createdImages.push(image);
        return image;
      }),
    );

    try {
      const first = loadCachedImage('/sample-d.jpg');
      expect(createdImages).toHaveLength(1);
      createdImages[0].onload?.();
      await first;
      await Promise.resolve();

      const second = loadCachedImage('/sample-d.jpg');
      expect(createdImages).toHaveLength(1);
      await expect(second).resolves.toBe(createdImages[0]);
    } finally {
      vi.unstubAllGlobals();
      clearImageCache();
    }
  });

  it('decodes the next image and keeps only a small decoded window', async () => {
    clearImageCache();

    const createdImages: MockImage[] = [];
    vi.stubGlobal(
      'Image',
      vi.fn(function () {
        const image: MockImage = {
          decode: vi.fn(() => Promise.resolve()),
        };
        createdImages.push(image);
        return image;
      }),
    );

    try {
      const first = decodeImage('/sample-e.jpg');
      createdImages[0].onload?.();
      await first;
      expect(createdImages[0].decode).toHaveBeenCalledTimes(1);

      const second = loadCachedImage('/sample-e.jpg');
      expect(createdImages).toHaveLength(1);
      await expect(second).resolves.toBe(createdImages[0]);

      for (const url of [
        '/sample-f.jpg',
        '/sample-g.jpg',
        '/sample-h.jpg',
        '/sample-i.jpg',
        '/sample-j.jpg',
        '/sample-k.jpg',
        '/sample-l.jpg',
        '/sample-m.jpg',
        '/sample-n.jpg',
        '/sample-o.jpg',
        '/sample-p.jpg',
        '/sample-q.jpg',
      ]) {
        const promise = decodeImage(url);
        createdImages[createdImages.length - 1].onload?.();
        await promise;
      }

      const evicted = loadCachedImage('/sample-e.jpg');
      expect(createdImages).toHaveLength(14);
      createdImages[6].onload?.();
      await evicted;
    } finally {
      vi.unstubAllGlobals();
      clearImageCache();
    }
  });
});
