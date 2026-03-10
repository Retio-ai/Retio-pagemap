/**
 * Make spoofed functions appear native via toString().
 *
 * Wraps a function so that Function.prototype.toString returns
 * "function <name>() { [native code] }" — matching real native methods.
 */

const _nativeToString = Function.prototype.toString;
const _spoofedFns = new WeakSet<Function>();

export function spoofToString<T extends Function>(fn: T, nativeName: string): T {
  _spoofedFns.add(fn);

  // Store the expected toString result on the function itself
  Object.defineProperty(fn, '_nativeStr', {
    value: `function ${nativeName}() { [native code] }`,
    enumerable: false,
    configurable: false,
    writable: false,
  });

  return fn;
}

/**
 * Install the global toString override (call once at init).
 * Intercepts Function.prototype.toString for spoofed functions.
 */
export function installToStringProxy(): void {
  Function.prototype.toString = function (this: Function): string {
    if (_spoofedFns.has(this)) {
      return (this as unknown as { _nativeStr: string })._nativeStr;
    }
    return _nativeToString.call(this);
  };
  // Make the override itself look native
  _spoofedFns.add(Function.prototype.toString);
  Object.defineProperty(Function.prototype.toString, '_nativeStr', {
    value: 'function toString() { [native code] }',
    enumerable: false,
    configurable: false,
    writable: false,
  });
}
