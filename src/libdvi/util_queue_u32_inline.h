#ifndef _UTIL_QUEUE_U32_INLINE_H
#define _UTIL_QUEUE_U32_INLINE_H

// Faster versions of the functions found in pico/util/queue.h, for the common
// case of 32-bit-sized elements. Can be used on the same queue data
// structure, and mixed freely with the generic access methods, as long as
// element_size == 4.

#include "pico/util/queue.h"
#include "hardware/sync.h"

// LOCAL PATCH (not upstream, see PROVENANCE.md): every function here is
// __force_inline (upstream: plain `static inline`) and the queue level is
// computed locally instead of via queue_get_level_unsafe(). Otherwise the
// compiler may emit out-of-line copies in flash, and the DVI IRQ/producer
// on core1 must not execute from flash (it keeps running while core0
// writes flash).

static __force_inline uint _queue_get_level_u32(queue_t *q) {
    int32_t rc = (int32_t)q->wptr - (int32_t)q->rptr;
    if (rc < 0) {
        rc += q->element_count + 1;
    }
    return (uint)rc;
}

static __force_inline uint16_t _queue_inc_index_u32(queue_t *q, uint16_t index) {
    if (++index > q->element_count) { // > because we have element_count + 1 elements
        index = 0;
    }
    return index;
}

static __force_inline bool queue_try_add_u32(queue_t *q, void *data) {
    bool success = false;
    uint32_t flags = spin_lock_blocking(q->core.spin_lock);
    if (_queue_get_level_u32(q) != q->element_count) {
        ((uint32_t*)q->data)[q->wptr] = *(uint32_t*)data;
        q->wptr = _queue_inc_index_u32(q, q->wptr);
        success = true;
    }
    spin_unlock(q->core.spin_lock, flags);
    if (success) __sev();
    return success;
}

static __force_inline bool queue_try_remove_u32(queue_t *q, void *data) {
    bool success = false;
    uint32_t flags = spin_lock_blocking(q->core.spin_lock);
    if (_queue_get_level_u32(q) != 0) {
        *(uint32_t*)data = ((uint32_t*)q->data)[q->rptr];
        q->rptr = _queue_inc_index_u32(q, q->rptr);
        success = true;
    }
    spin_unlock(q->core.spin_lock, flags);
    if (success) __sev();
    return success;
}

static __force_inline bool queue_try_peek_u32(queue_t *q, void *data) {
    bool success = false;
    uint32_t flags = spin_lock_blocking(q->core.spin_lock);
    if (_queue_get_level_u32(q) != 0) {
        *(uint32_t*)data = ((uint32_t*)q->data)[q->rptr];
        success = true;
    }
    spin_unlock(q->core.spin_lock, flags);
    return success;
}

static __force_inline void queue_add_blocking_u32(queue_t *q, void *data) {
    bool done;
    do {
        done = queue_try_add_u32(q, data);
        if (done) break;
        __wfe();
    } while (true);
}

static __force_inline void queue_remove_blocking_u32(queue_t *q, void *data) {
    bool done;
    do {
        done = queue_try_remove_u32(q, data);
        if (done) break;
        __wfe();
    } while (true);
}

static __force_inline void queue_peek_blocking_u32(queue_t *q, void *data) {
    bool done;
    do {
        done = queue_try_peek_u32(q, data);
        if (done) break;
        __wfe();
    } while (true);
}

#endif
