/**
 * Composable module registry for Ask AI.
 *
 * The panel itself is the legacy Ask AI surface and owns the thread, the
 * composer, page context, record chips, follow-ups and the trust affordances.
 * Anything a newer protocol adds on top of that surface is a module registered
 * here. Every module is independent and may be switched off without affecting
 * the panel or any other module, so a broken or unwanted surface is a config
 * change rather than a code change.
 *
 * A module is `{ name, mount(bus), unmount() }`. `mount` receives the event bus
 * the active protocol publishes to and returns nothing.
 */

const registered = new Map();
const mounted = new Map();

/** Registers a module definition. Registration alone does not mount it. */
export function registerModule(module) {
    if (!module?.name || typeof module.mount !== 'function') {
        return;
    }
    registered.set(module.name, module);
}

/** Names of every registered module, mounted or not. */
export function registeredModules() {
    return [...registered.keys()];
}

/** Names of the modules currently mounted. */
export function activeModules() {
    return [...mounted.keys()];
}

export function isEnabled(name) {
    return mounted.has(name);
}

/**
 * Mounts one registered module. Mounting an already-mounted or unknown module
 * is a no-op, so callers may enable idempotently.
 *
 * @returns {boolean} whether the module is mounted after the call
 */
export function enableModule(name, bus) {
    if (mounted.has(name)) {
        return true;
    }
    const module = registered.get(name);
    if (!module) {
        return false;
    }
    try {
        module.mount(bus);
    } catch (err) {
        // One failed module must not take the panel or its siblings down with
        // it. The panel keeps working without the surface this module adds.
        console.error(`[ask-ai] module "${name}" failed to mount`, err);
        return false;
    }
    mounted.set(name, module);
    return true;
}

/** Unmounts one module. Unknown or already-unmounted names are a no-op. */
export function disableModule(name) {
    const module = mounted.get(name);
    if (!module) {
        return false;
    }
    try {
        module.unmount?.();
    } catch (err) {
        console.error(`[ask-ai] module "${name}" failed to unmount`, err);
    }
    mounted.delete(name);
    return true;
}

/**
 * Mounts exactly the modules whose flag is true and unmounts the rest, so one
 * config object describes the whole surface.
 *
 * @param {Record<string, boolean>} flags - module name to enabled
 * @param {EventTarget} bus - the active protocol's event bus
 */
export function applyModuleFlags(flags, bus) {
    for (const name of registered.keys()) {
        if (flags?.[name]) {
            enableModule(name, bus);
        } else {
            disableModule(name);
        }
    }
}

/** Unmounts every module. Used when the panel closes. */
export function disableAllModules() {
    for (const name of [...mounted.keys()]) {
        disableModule(name);
    }
}
