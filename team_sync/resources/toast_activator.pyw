"""Windows toast activator for team-sync, copied to ~/.team-sync/notify/ and run by COM (with -Embedding) when someone
clicks a team-sync toast. It registers the activator class, takes the INotificationActivationCallback::Activate call
and answers S_OK, so Windows removes the clicked toast from the Action Center, then starts the agent app.
Standard library only (ctypes); the CLSID must match TOAST_CLSID in notifier.py."""
import ctypes
import os
import pathlib
import threading

CLSID = '{6B1E2A54-0F3C-4D8E-9A71-3C5D7E2B9F10}'
IUNKNOWN = '{00000000-0000-0000-C000-000000000046}'
ICLASSFACTORY = '{00000001-0000-0000-C000-000000000046}'
IACTIVATION = '{53E31837-6600-4A81-9395-75CFFE746F94}'           # INotificationActivationCallback
E_NOINTERFACE = -2147467262
CLSCTX_LOCAL_SERVER, REGCLS_MULTIPLEUSE, COINIT_MULTITHREADED = 4, 1, 0


class GUID(ctypes.Structure):
    _fields_ = [('d1', ctypes.c_ulong), ('d2', ctypes.c_ushort), ('d3', ctypes.c_ushort), ('d4', ctypes.c_ubyte * 8)]


def guid(s):
    g = GUID()
    ctypes.oledll.ole32.CLSIDFromString(s, ctypes.byref(g))
    return bytes(g)


class Obj(ctypes.Structure):                                      # a COM object is a pointer to its vtable
    _fields_ = [('vtbl', ctypes.c_void_p)]


QI = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
REF = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
CREATE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
LOCK = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int)
ACTIVATE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong)

done = threading.Event()
factory, callback = Obj(), Obj()


def _give(obj, wanted, riid, ppv):
    if bytes(riid.contents) in wanted:
        ppv[0] = ctypes.addressof(obj)
        return 0
    ppv[0] = None
    return E_NOINTERFACE


def _activate(this, app_id, args, data, count):
    done.set()
    return 0


# static objects: reference counting is a no-op; the process ends right after the click is handled
_addref = REF(lambda this: 1)
_release = REF(lambda this: 1)
_f_qi = QI(lambda this, riid, ppv: _give(factory, (guid(IUNKNOWN), guid(ICLASSFACTORY)), riid, ppv))
_create = CREATE(lambda this, outer, riid, ppv: _give(callback, (guid(IUNKNOWN), guid(IACTIVATION)), riid, ppv))
_lock = LOCK(lambda this, on: 0)
_c_qi = QI(lambda this, riid, ppv: _give(callback, (guid(IUNKNOWN), guid(IACTIVATION)), riid, ppv))
_act = ACTIVATE(_activate)
f_vtbl = (ctypes.c_void_p * 5)(*[ctypes.cast(f, ctypes.c_void_p) for f in (_f_qi, _addref, _release, _create, _lock)])
c_vtbl = (ctypes.c_void_p * 4)(*[ctypes.cast(f, ctypes.c_void_p) for f in (_c_qi, _addref, _release, _act)])
factory.vtbl, callback.vtbl = ctypes.addressof(f_vtbl), ctypes.addressof(c_vtbl)


def main():
    ole = ctypes.oledll.ole32
    try:
        ole.CoInitializeEx(None, COINIT_MULTITHREADED)
        clsid, cookie = GUID(), ctypes.c_ulong()
        ole.CLSIDFromString(CLSID, ctypes.byref(clsid))
        ole.CoRegisterClassObject(ctypes.byref(clsid), ctypes.byref(factory), CLSCTX_LOCAL_SERVER, REGCLS_MULTIPLEUSE, ctypes.byref(cookie))
        done.wait(10)                                             # COM started us for a click: Activate arrives within a moment
        ole.CoRevokeClassObject(cookie)
    except OSError:
        pass                                                      # still open the agent: the person clicked
    p = pathlib.Path.home() / '.team-sync' / 'notify' / 'agent'
    exe = p.read_text(encoding='utf-8').strip().lstrip('﻿') if p.exists() else ''
    if exe and os.path.exists(exe):
        os.startfile(exe)


if __name__ == '__main__':
    main()
