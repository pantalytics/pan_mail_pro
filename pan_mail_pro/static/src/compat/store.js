/** @odoo-module */
// The mail store keys its record models by class name on 18 and 19
// (`store.Thread`) and by model name on 20 (`store["mail.thread"]`).
export function threadModel(store) {
    return storeModel(store, "Thread", "mail.thread");
}

/** A store model by its class name (18, 19) or its model name (20). */
export function storeModel(store, className, modelName) {
    return store[className] || store[modelName];
}

/** Every record of a store model: an object up to 19, a Map on 20. */
export function storeRecords(model) {
    const records = model.records;
    return records instanceof Map ? [...records.values()] : Object.values(records);
}

/**
 * Load what the thread's follower list needs. 19 and 20 fetch them by name;
 * 18 has only the "load more" page, which starts after the last follower it
 * has and reads its id off nothing on a fresh thread, so there the first
 * page is asked for the way that method asks for the next one.
 */
export async function fetchFollowers(thread, orm) {
    if (!thread) {
        return;
    }
    if (thread.fetchThreadData) {
        return thread.fetchThreadData(["followers"]);
    }
    const data = await orm.call(thread.model, "message_get_followers", [[thread.id], false]);
    thread.store.insert(data);
}
