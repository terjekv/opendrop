from abc import abstractmethod
from typing import Any, Generic, TypeVar, Sequence, Optional, Callable, Type, MutableSequence, Iterable

from opendrop.utility.events import Event, EventConnection
from .binding import Binding
from .node import Source, Sink

TxT1 = TypeVar('TxT1')
TxT2 = TypeVar('TxT2')
VT = TypeVar('VT')
T = TypeVar('T')


# Bindable

class Bindable(Source[TxT1], Sink[TxT2]):
    def __init__(self):
        self.on_new_tx = Event()

    def _apply_tx(self, tx: TxT2, block: Sequence[EventConnection] = tuple()):
        new_txs = self._raw_apply_tx(tx)
        new_txs = [tx] if new_txs is None else new_txs

        for new_tx in new_txs:
            self._bcast_tx(new_tx, block=block)

    def _bcast_tx(self, tx: TxT1, block: Sequence[EventConnection] = tuple()) -> None:
        self.on_new_tx.fire_with_opts(args=(tx,), block=block)

    def bind_from(self, src: 'Bindable[TxT2, TxT1]') -> Binding[TxT2, TxT1]:
        return Binding(src=src, dst=self)

    def bind_to(self, dst: 'Bindable[TxT2, TxT1]') -> Binding[TxT1, TxT2]:
        return Binding(src=self, dst=dst)

    @abstractmethod
    def _export(self) -> TxT1:
        """Return a transaction that can be applied to another Bindable of the same type to restore its state to this
        Bindable's state.
        """
        pass

    @abstractmethod
    def _raw_apply_tx(self, tx: TxT1) -> Optional[Sequence[TxT2]]:
        """Apply `tx` and return a sequence of transactions that should be broadcasted as a result of the changes made
        during transaction application. Optionally return None to specify that the same `tx` should be broadcasted.
        Return an empty sequence to specify that no transactions should be broadcasted.
        """
        pass


# AtomicBindable

class AtomicBindableTx(Generic[VT]):
    value = None  # type: VT

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, AtomicBindableTx):
            return False

        return self.value == other.value


class AtomicBindable(Bindable[AtomicBindableTx[VT], AtomicBindableTx[VT]]):
    class _AtomicBindablePropertyAdapter(Generic[T, VT]):
        def __init__(self, bn_getter: Callable[[T], 'AtomicBindable[VT]']):
            self._bn_getter = bn_getter

        def __get__(self, instance: T, owner: Type[T]) -> VT:
            if instance is None:
                return self

            return self._bn_getter(instance).get()

        def __set__(self, instance: T, value: VT) -> None:
            self._bn_getter(instance).set(value)

    property_adapter = _AtomicBindablePropertyAdapter

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.on_changed = Event()

    @abstractmethod
    def get(self) -> VT:
        """Get the value of this atomic bindable"""

    @abstractmethod
    def set(self, value: VT) -> None:
        """Set the value of this atomic bindable"""


# BaseAtomicBindable

class BaseAtomicBindableTx(AtomicBindableTx[VT]):
    def __init__(self, value: VT):
        self.value = value


class BaseAtomicBindable(AtomicBindable[VT]):
    def poke(self) -> None:
        """Force this BaseAtomicBindable (BAB) to fire its `on_new_tx` event with a transaction representing the
        current value of this BAB. Also fires its `on_changed` event. Usually called when the underlying value of this
        BAB has changed, but this change was not made using BAB.set().
        """
        self._value_changed(self._raw_get(), bcast_tx=True)

    def _set(self, value: VT, bcast_tx: bool) -> None:
        self._raw_set(value)
        self._value_changed(value, bcast_tx=bcast_tx)

    def _value_changed(self, new_value: VT, bcast_tx: bool) -> None:
        self.on_changed.fire()
        if bcast_tx:
            self._bcast_tx(self._create_tx(new_value))

    @staticmethod
    def _create_tx(value: VT) -> AtomicBindableTx[VT]:
        """Create and return a new transaction that when applied to another BaseAtomicBindable `bn`, should set the
        value that `bn` is storing to `value`.
        """
        return BaseAtomicBindableTx(value)

    # AtomicBindable abstract methods implementation:

    def get(self) -> VT:
        return self._raw_get()

    def set(self, value: VT) -> None:
        self._set(value, bcast_tx=True)

    # Bindable abstract methods implementation:

    def _export(self) -> AtomicBindableTx[VT]:
        return self._create_tx(self._raw_get())

    def _raw_apply_tx(self, tx: AtomicBindableTx[VT]):
        self._set(tx.value, bcast_tx=False)

    # My abstract methods:

    # Quick note, require that bn._raw_set(some_value) -> bn._raw_get() == some_value.
    @abstractmethod
    def _raw_get(self) -> VT:
        pass

    @abstractmethod
    def _raw_set(self, value: VT) -> None:
        pass


# AtomicBindableVar

class AtomicBindableVar(BaseAtomicBindable[VT]):
    def __init__(self, initial: VT):
        super().__init__()
        self._value = initial

    def _raw_get(self) -> VT:
        return self._value

    def _raw_set(self, value: VT) -> None:
        self._value = value


# AtomicBindableAdapter

class AtomicBindableAdapter(BaseAtomicBindable[VT]):
    def __init__(self, getter: Optional[Callable[[], VT]] = None, setter: Optional[Callable[[VT], None]] = None):
        super().__init__()

        # Quick note, poke() should be called whenever the value that is returned by getter is changed.
        self.getter = getter
        self.setter = setter

    def _raw_get(self) -> VT:
        if self.getter is None:
            raise AttributeError("Unreadable bindable (no getter)")

        return self.getter()

    def _raw_set(self, value: VT) -> None:
        if self.setter is None:
            raise AttributeError("Can't set bindable (no setter)", self, value)

        self.setter(value)


# MutableSequenceBindable
class MutableSequenceBindableTx(Generic[VT]):
    @abstractmethod
    def silent_apply(self, target: 'MutableSequenceBindable[VT]') -> None:
        """Apply the transaction onto `target` without `target` broadcasting new transactions."""


class MutableSequenceBindableSetItemTx(MutableSequenceBindableTx[VT]):
    def __init__(self, i: int, v: VT) -> None:
        self._i = i
        self._v = v

    def silent_apply(self, target: 'MutableSequenceBindable[VT]') -> None:
        target.__setitem__(self._i, self._v, _bcast_tx=False)


class MutableSequenceBindableDelItemTx(MutableSequenceBindableTx[VT]):
    def __init__(self, i: int) -> None:
        self._i = i

    def silent_apply(self, target: 'MutableSequenceBindable[VT]') -> None:
        target.__delitem__(self._i, _bcast_tx=False)


class MutableSequenceBindableInsertTx(MutableSequenceBindableTx[VT]):
    def __init__(self, i: int, v: VT) -> None:
        self._i = i
        self._v = v

    def silent_apply(self, target: 'MutableSequenceBindable[VT]') -> None:
        target.insert(self._i, self._v, _bcast_tx=False)


class MutableSequenceBindableGroupedTx(MutableSequenceBindableTx[VT]):
    def __init__(self, txs: Iterable[MutableSequenceBindableTx[VT]]) -> None:
        self._txs = list(txs)

    def silent_apply(self, target: 'MutableSequenceBindable[VT]') -> None:
        for tx in self._txs:
            tx.silent_apply(target)


class MutableSequenceBindable(Bindable[MutableSequenceBindableTx[VT], MutableSequenceBindableTx[VT]], MutableSequence[VT]):
    def __init__(self) -> None:
        super().__init__()

        self.on_setitem = Event()
        self.on_delitem = Event()
        self.on_insert = Event()

    def __getitem__(self, i: int) -> VT:
        return self._real_getitem(i)

    def __setitem__(self, i: int, v: VT, _bcast_tx: bool = True) -> None:
        self._real_setitem(i, v)
        self.on_setitem.fire(i, v)
        if _bcast_tx:
            self._bcast_tx(self._create_setitem_tx(i, v))

    def __delitem__(self, i: int, _bcast_tx: bool = True) -> None:
        self._real_delitem(i)
        self.on_delitem.fire(i)
        if _bcast_tx:
            self._bcast_tx(self._create_delitem_tx(i))

    def insert(self, i: int, v: VT, _bcast_tx: bool = True) -> None:
        self._real_insert(i, v)
        self.on_insert.fire(i, v)
        if _bcast_tx:
            self._bcast_tx(self._create_insert_tx(i, v))

    def _export(self) -> MutableSequenceBindableTx:
        return MutableSequenceBindableGroupedTx(
            self._create_insert_tx(i, v) for i, v in enumerate(self)
        )

    def _raw_apply_tx(self, tx: MutableSequenceBindableTx) -> None:
        tx.silent_apply(self)

    @staticmethod
    def _create_setitem_tx(i: int, v: VT) -> MutableSequenceBindableTx:
        return MutableSequenceBindableSetItemTx(i, v)

    @staticmethod
    def _create_delitem_tx(i: int) -> MutableSequenceBindableTx:
        return MutableSequenceBindableDelItemTx(i)

    @staticmethod
    def _create_insert_tx(i: int, v: VT) -> MutableSequenceBindableTx:
        return MutableSequenceBindableInsertTx(i, v)

    @abstractmethod
    def _real_setitem(self, i: int, v: VT) -> None:
        """Actual implementation of __setitem__"""

    @abstractmethod
    def _real_getitem(self, i: int) -> VT:
        """Actual implementation of __getitem__"""

    @abstractmethod
    def _real_delitem(self, i: int) -> None:
        """Actual implementation of __delitem__"""

    @abstractmethod
    def _real_insert(self, i: int, v: VT) -> None:
        """Actual implementation of insert"""


class ListBindable(MutableSequenceBindable[VT]):
    def __init__(self, initial: Optional[Iterable[VT]] = None) -> None:
        super().__init__()
        self._list = list(initial) if initial is not None else []

    def _real_getitem(self, i: int) -> VT:
        return self._list[i]

    def _real_setitem(self, i: int, v: VT) -> None:
        self._list[i] = v
        return

    def _real_delitem(self, i: int) -> None:
        del self._list[i]

    def _real_insert(self, i: int, v: VT) -> None:
        self._list.insert(i, v)

    def __len__(self) -> int:
        return len(self._list)

    def __str__(self) -> str:
        return str(self._list)

    def __repr__(self) -> str:
        return '{}({!r})'.format(type(self).__name__, self._list)
