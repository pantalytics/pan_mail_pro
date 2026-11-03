/** @odoo-module */
// The Owl 3 version of pan_mail_pro/static/src/compat/owl.js (Odoo 20); the
// comment there says what each name stands in for.
import { __info__, proxy, signal, useOnChange } from "@odoo/owl";

export { useProps } from "@odoo/owl";

export { useSubEnv } from "@web/owl2/utils";

export const OWL_MAJOR = parseInt(__info__.version.split(".")[0], 10);

/** Reactive state: Owl 3 tracks what a render reads. */
export function useState(value) {
    return proxy(value);
}

/** A ref the template names as `t-ref="this.<attr>"`: a signal, read as `.el`. */
export function useRef() {
    const ref = signal.ref();
    Object.defineProperty(ref, "el", { get: () => ref() });
    return ref;
}

/** Owl 2's `useEffect(fn, deps)`: run `fn` with the dependencies when they change. */
export function useEffect(fn, deps) {
    useOnChange(deps || (() => []), fn);
}

/** Owl 3 validates no static props; the schema is kept for the series that do. */
export function defineProps() {}
