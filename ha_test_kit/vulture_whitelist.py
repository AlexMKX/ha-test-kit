"""Whitelist for vulture to suppress HA framework conventions.

Vulture does not understand that these names are called by Home
Assistant itself (config flow dispatcher, entity platform, pydantic
validators, etc.), not by in-repo code. Listing them here marks
them as "used" for vulture's cross-file analysis.

Pattern: any symbol that HA or pydantic calls by convention goes
here. Project-specific dead code should still be caught — do not
add specific function names from the consuming project.
"""

# Home Assistant integration lifecycle
async_setup_entry  # noqa: F821
async_unload_entry  # noqa: F821
async_migrate_entry  # noqa: F821
async_remove_config_entry_device  # noqa: F821
async_setup  # noqa: F821

# HA config flow
VERSION  # noqa: F821
async_step_user  # noqa: F821
async_step_init  # noqa: F821
async_step_import  # noqa: F821
async_get_options_flow  # noqa: F821

# HA entity API
device_info  # noqa: F821
extra_state_attributes  # noqa: F821
async_added_to_hass  # noqa: F821
async_will_remove_from_hass  # noqa: F821
async_set_value  # noqa: F821
native_value  # noqa: F821

# HA entity _attr_* shortcuts
_attr_name  # noqa: F821
_attr_unique_id  # noqa: F821
_attr_is_on  # noqa: F821
_attr_native_value  # noqa: F821
_attr_should_poll  # noqa: F821
_attr_device_class  # noqa: F821
_attr_icon  # noqa: F821
_attr_has_entity_name  # noqa: F821

# pydantic v2
model_config  # noqa: F821
