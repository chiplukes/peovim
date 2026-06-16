"""
api — public plugin API surface

The only module plugins should import from. Re-exports all API namespaces.
Plugins receive an instance of EditorAPI via their setup(api) function.
See notes/api.md for the plugin-facing API surface.
"""
