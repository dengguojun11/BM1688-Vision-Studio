declare interface Window {
  __REDUX_DEVTOOLS_EXTENSION_COMPOSE__: () => any;
  __backend?: {
    version?: string;
    build_time?: string;
    name?: string;
    commit?: string;
  };
  __frontend?: {
    version?: string;
    deps?: Record<string, string>;
  };
}

declare interface ObjectConstructor {
  keys: <T>(o: T) => (keyof T)[];
}
