import '@labelu/image';
import '@labelu/image-annotator-react';

declare module '@labelu/image' {
  export function decodeImage(url?: string | null, priority?: 'low' | 'high' | 'auto'): Promise<undefined>;
  export function preloadImage(url?: string | null): void;
}

declare module '@labelu/image-annotator-react' {
  import type { Annotator as ImageAnnotatorClass } from '@labelu/image';
  import type { AllAnnotationType } from '@labelu/image-annotator-react/dist/context/annotation.context';

  interface ImageAnnotatorProps {
    overwriteExistingAnnotations?: boolean;
    replacePreAnnotationTools?: AllAnnotationType[];
    onReady?: (engine: ImageAnnotatorClass, url?: string | null) => void;
  }
}
