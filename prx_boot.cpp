/*
 * Load the lifted libsre (Sony's cellSpurs/cellSync) at startup.
 *
 * Compiled only when the port has a lifted libsre in PORT_PRX_DIR (see
 * scripts/lift_libsre.py and CMakeLists.txt). The runtime's import dispatcher
 * (runtime/ppu/ppu_hle.cpp) asks prx_resolve_export() before its own HLE
 * handlers, so once the exports are published here every cellSpurs/cellSync
 * import the game makes runs the real library instead of the reimplementation.
 *
 *   PS3_NO_LIBSRE=1    skip it and use the HLE SPURS, to compare the two
 *   PS3_LIBSRE=<path>  load a different libsre.linked.bin
 */
#include <cstdio>
#include <cstdint>
#include <cstdlib>

#include "prx_loader.h"

extern "C" {
extern const prx_func_entry libsre_function_table[];
extern const uint64_t       libsre_function_table_count;
extern const prx_export     libsre_exports[];
extern const uint32_t       libsre_export_count;
void ppu_register_function(uint64_t addr, void (*fn)(void*));
}

#ifndef PORT_PRX_DIR
#define PORT_PRX_DIR "prx"
#endif

static const uint32_t kLibsreBase = 0x30000000u;   /* scripts/lift_libsre.py's BASE */
static uint32_t       s_libsre_size = 0;
static unsigned       s_skipped = 0;

/* The lifter can emit table entries outside the module (data in the code
 * segment decoded as a branch target). Registering one would replace the
 * GAME's function at that guest address, so only libsre's own range is taken. */
static void register_inside(uint32_t addr, void (*fn)(void*))
{
    if (addr >= kLibsreBase && addr < kLibsreBase + s_libsre_size)
        ppu_register_function(addr, fn);
    else
        s_skipped++;
}

extern "C" void ps3_load_prx_modules(void)
{
    if (getenv("PS3_NO_LIBSRE")) {
        fprintf(stderr, "[prx] PS3_NO_LIBSRE set -- using HLE SPURS\n");
        return;
    }
    const char* path = getenv("PS3_LIBSRE");
    char buf[1024];
    if (!path || !*path) {
        snprintf(buf, sizeof buf, "%s/libsre.linked.bin", PORT_PRX_DIR);
        path = buf;
    }

    uint32_t size = 0;
    uint8_t* image = prx_image_load_file(path, &size);
    if (!image) {
        fprintf(stderr, "[prx] no libsre image at %s -- using HLE SPURS\n", path);
        return;
    }
    s_libsre_size = size;

    prx_module m = {};
    m.name         = "libsre";
    m.base         = kLibsreBase;
    m.image        = image;
    m.image_size   = size;
    m.funcs        = libsre_function_table;
    m.func_count   = libsre_function_table_count;
    m.exports      = libsre_exports;
    m.export_count = libsre_export_count;
    prx_load_result r = prx_load_module(&m, register_inside);
    if (!r.ok)
        fprintf(stderr, "[prx] libsre load FAILED -- using HLE SPURS\n");
    else
        fprintf(stderr, "[prx] libsre: %u lifted functions registered (%u outside the module skipped)\n",
                r.funcs_registered - s_skipped, s_skipped);
    free(image);   /* prx_load_module copied it into guest RAM */
}
