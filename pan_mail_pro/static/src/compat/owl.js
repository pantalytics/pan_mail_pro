/** @odoo-module */
// The Owl half of the series seam (docs/plans/multi-version.md). 18 and 19
// run Owl 2; 20 runs Owl 3 and tools/series/20.0 carries this file's Owl 3
// version. The module imports these names from here and nothing else of
// what the two differ in:
//
// - `useState`: Owl 3 has `proxy` and tracks reads on render.
// - `useRef`: Owl 3 wants a signal passed to `t-ref` as an expression, so a
//   template says `t-ref="this.rootRef"` on every series: Owl 2 reads that
//   as the literal name this hook registers, Owl 3 evaluates it.
// - `useSubEnv`: Owl 3 has it in Odoo's own compatibility layer.
// - `useEffect(fn, deps)`: Owl 3 tracks dependencies itself; `useOnChange`
//   is the one that takes a dependency function.
// - `useProps()`: Owl 3 hands a component its props only through it, so
//   every component's setup starts with `this.props = useProps()`; Owl 2 has
//   them on the instance already.
// - `static props`: Owl 3 throws on it (and wants `useProps`, which Owl 2
//   does not have), so the schema is `static propsSchema` and
//   `defineProps(Class)` enforces it where Owl still does.
import { __info__, useComponent, useRef as owlUseRef } from "@odoo/owl";

export { useState, useSubEnv, useEffect } from "@odoo/owl";

export const OWL_MAJOR = parseInt(__info__.version.split(".")[0], 10);

/** The component's props, as the instance already carries them. */
export function useProps() {
    return useComponent().props;
}

/** A ref the template names as `t-ref="this.<attr>"`. */
export function useRef(attr) {
    return owlUseRef(`this.${attr}`);
}

/** Validate `Class.propsSchema` where Owl still validates (Owl 2). */
export function defineProps(Class) {
    if (Class.propsSchema) {
        Class.props = Class.propsSchema;
    }
}
