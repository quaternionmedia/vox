"""Adapters, each named for the thing it targets, none of them required.

The rest of vox states what a speech engine and a synthesizer must do. This
package is where a particular one gets named, and naming it is the module's
whole job — so a reader can tell at a glance which parts of vox are a
contract and which are a binding to somebody's product.

Nothing in `vox` imports this package. A caller picks an adapter, or writes
one: an engine adapter is an `EngineContract` value, and a synthesizer
adapter is any object with `speak(text, out_path=None) -> str`.
"""

from vox.adapters.joe import JOE

__all__ = ["JOE"]
