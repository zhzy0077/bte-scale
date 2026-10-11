#!/usr/bin/env python3
"""Run unmodified APK arithmetic code on an AArch64 Linux host for verification.

Maps PT_LOAD segments and resolves the small ELF relocation table. No Android
runtime, firmware changes, on-disk binary patching, or JNI callbacks are needed.
The library's bia_calc uses a global pointer; use this oracle serially.
"""
import ctypes
import hashlib
import mmap
import platform
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / 'analysis/native/libcsalgorithm.so'
SHA256 = '47a17e688d8122724452348bc61de71c0a9b75836b05f75468bc73b4b3b7ca42'


class NativeOracle:
    def __init__(self, path=LIBRARY):
        if platform.machine() != 'aarch64':
            raise RuntimeError('The native oracle requires an AArch64 Linux host')
        data = Path(path).read_bytes()
        if hashlib.sha256(data).hexdigest() != SHA256:
            raise ValueError('Unexpected libcsalgorithm.so version/hash')
        header = struct.unpack_from('<16sHHIQQQIHHHHHH', data)
        ident, _, machine, _, _, phoff, shoff, _, _, phsize, phnum, shsize, shnum, _ = header
        if ident[:6] != b'\x7fELF\x02\x01' or machine != 183:
            raise ValueError('Expected little-endian ELF64 AArch64')
        programs = [struct.unpack_from('<IIQQQQQQ', data, phoff+i*phsize) for i in range(phnum)]
        sections = [struct.unpack_from('<IIQQQQIIQQ', data, shoff+i*shsize) for i in range(shnum)]
        loads = [p for p in programs if p[0] == 1]
        size = max(p[3]+p[6] for p in loads)
        page = mmap.PAGESIZE
        size = (size+page-1)//page*page
        self.memory = mmap.mmap(-1, size, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS,
                                prot=mmap.PROT_READ | mmap.PROT_WRITE)
        self.base = ctypes.addressof(ctypes.c_char.from_buffer(self.memory))
        for _, _, offset, address, _, filesz, _, _ in loads:
            self.memory[address:address+filesz] = data[offset:offset+filesz]
        dynsym = next(s for s in sections if s[1] == 11)
        strings_section = sections[dynsym[6]]
        strings = data[strings_section[4]:strings_section[4]+strings_section[5]]
        symbols = []
        self.symbols = {}
        for offset in range(dynsym[4], dynsym[4]+dynsym[5], dynsym[9]):
            name, info, other, index, value, size = struct.unpack_from('<IBBHQQ', data, offset)
            end = strings.index(b'\x00', name)
            name = strings[name:end].decode()
            symbols.append((name, index, value))
            if index:
                self.symbols[name] = self.base+value
        self.libc = ctypes.CDLL(None)
        for section in sections:
            if section[1] != 4:  # SHT_RELA
                continue
            for offset in range(section[4], section[4]+section[5], section[9]):
                address, info, addend = struct.unpack_from('<QQq', data, offset)
                symbol_index, kind = info >> 32, info & 0xffffffff
                if kind == 1027:  # R_AARCH64_RELATIVE
                    value = self.base+addend
                elif kind in (1025, 1026):  # GLOB_DAT / JUMP_SLOT
                    name, index, symbol_value = symbols[symbol_index]
                    value = (self.base+symbol_value if index else
                             ctypes.cast(getattr(self.libc, name), ctypes.c_void_p).value)
                    value += addend
                else:
                    raise ValueError(f'Unsupported relocation: {kind}')
                struct.pack_into('<Q', self.memory, address, value)
        self.libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        self.libc.mprotect.restype = ctypes.c_int
        for _, flags, _, address, _, _, memsz, _ in loads:
            start = address//page*page
            end = (address+memsz+page-1)//page*page
            protection = (mmap.PROT_READ if flags & 4 else 0)
            protection |= mmap.PROT_WRITE if flags & 2 else 0
            protection |= mmap.PROT_EXEC if flags & 1 else 0
            if self.libc.mprotect(self.base+start, end-start, protection):
                raise OSError('mprotect failed')
        prefix = 'Java_com_chipsea_health_CSAlgorithmUtils_'
        resistance_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.c_float, ctypes.c_byte, ctypes.c_float,
                                          ctypes.c_int, ctypes.c_float)
        self._resistance = resistance_type(self.symbols[prefix+'getResistance'])
        vfr_type = ctypes.CFUNCTYPE(ctypes.c_float, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_float, ctypes.c_byte, ctypes.c_float,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int)
        self._vfr = vfr_type(self.symbols[prefix+'getVFR'])

    def resistance(self, height_cm, algorithm_gender, weight_kg, age, body_fat_percent):
        return self._resistance(None, None, height_cm, algorithm_gender, weight_kg,
                                age, body_fat_percent)

    def vfr(self, height_cm, algorithm_gender, weight_kg, age, resistance, year=2026):
        return self._vfr(None, None, height_cm, algorithm_gender, weight_kg,
                         age, resistance, year)


if __name__ == '__main__':
    import json
    oracle = NativeOracle()
    resistance = oracle.resistance(170, 0, 70, 40, 20)
    print(json.dumps({'library_sha256': SHA256, 'resistance': resistance,
                      'visceral_fat_level': oracle.vfr(170, 0, 70, 40, resistance)}, indent=2))
