import * as react from "react";
import * as reactDom from "react-dom";
import * as jsx from "react/jsx-runtime";
import * as sdk from "./sdk";

/** Public build ABI v1. External modules receive these exact host instances. */
export function providePackRuntime() {
  const key = Symbol.for("oaw.frontend.host.v1");
  const target = globalThis as typeof globalThis & { [key: symbol]: unknown };
  if (!target[key]) Object.defineProperty(target, key, {
    value: Object.freeze({ react, reactDom, jsx, sdk }), configurable: false, writable: false,
  });
  return target[key];
}
