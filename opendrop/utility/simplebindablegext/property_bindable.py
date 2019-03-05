import weakref
from typing import TypeVar, Callable, Any

from gi.repository import GObject

from opendrop.utility.simplebindable import Bindable

_T = TypeVar('_T')


class GObjectPropertyBindable(Bindable[_T]):
    def __init__(self, g_obj: GObject.Object, prop_name: str, transform_to: Callable[[_T], Any] = lambda x: x,
                 transform_from: Callable[[Any], _T] = lambda x: x) -> None:
        super().__init__()

        self._alive = True

        # For some reason, it seems like the GObject can be garbage collected before this object is garbage collected,
        # the following workaround uses _g_obj_wr to check if g_obj has been garbage collected by seeing if _g_obj_wr()
        # returns None.
        self._g_obj = g_obj
        self._g_obj_wr = weakref.ref(g_obj)

        self._prop_name = prop_name

        self._transform_to = transform_to
        self._transform_from = transform_from

        self._hdl_g_obj_notify_id = self._g_obj.connect('notify::{}'.format(prop_name), self._hdl_g_obj_notify)

    def _hdl_g_obj_notify(self, g_obj: GObject.Object, pspec: GObject.GParamSpec) -> None:
        if not self._alive:
            return

        self.on_changed.fire()

    def _get_value(self) -> Any:
        assert self._alive

        value = self._g_obj.get_property(self._prop_name)
        value = self._transform_from(value)

        return value

    def _set_value(self, new_value: Any) -> None:
        assert self._alive

        self._g_obj.handler_block(self._hdl_g_obj_notify_id)

        try:
            new_value = self._transform_to(new_value)
            self._g_obj.set_property(self._prop_name, new_value)
        finally:
            self._g_obj.handler_unblock(self._hdl_g_obj_notify_id)

    def _unlink(self, *_):
        if not self._alive:
            return

        if not self._is_g_obj_garbage_collected and self._g_obj.handler_is_connected(self._hdl_g_obj_notify_id):
            self._g_obj.disconnect(self._hdl_g_obj_notify_id)

        self._alive = False

    @property
    def _is_g_obj_garbage_collected(self) -> bool:
        return self._g_obj_wr() is None

    def __del__(self):
        self._unlink()
