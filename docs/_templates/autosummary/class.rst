{%- set pydantic_method_names = [
   'construct',
   'copy',
   'dict',
   'from_orm',
   'json',
   'model_construct',
   'model_copy',
   'model_dump',
   'model_dump_json',
   'model_json_schema',
   'model_parametrized_name',
   'model_post_init',
   'model_rebuild',
   'model_validate',
   'model_validate_json',
   'model_validate_strings',
   'parse_file',
   'parse_obj',
   'parse_raw',
   'schema',
   'schema_json',
   'update_forward_refs',
   'validate',
] -%}
{%- set pydantic_attribute_names = [
   'model_computed_fields',
   'model_config',
   'model_extra',
   'model_fields',
   'model_fields_set',
] -%}
{%- set is_pydantic_model = 'model_dump' in inherited_members and 'model_fields' in attributes -%}
{%- if is_pydantic_model -%}
{%- set summary_methods = methods | reject('in', inherited_members) | reject('in', pydantic_method_names) | list -%}
{%- set summary_attributes = attributes | reject('in', pydantic_attribute_names) | list -%}
{%- else -%}
{%- set summary_methods = methods -%}
{%- set summary_attributes = attributes -%}
{%- endif -%}
{{ fullname | escape | underline}}

.. currentmodule:: {{ module }}

.. autoclass:: {{ objname }}

   {% block methods %}
   {% if not is_pydantic_model or '__init__' not in inherited_members %}
   .. automethod:: __init__
   {% endif %}

   {% if summary_methods %}
   .. rubric:: {{ _('Methods') }}

   .. autosummary::
   {% for item in summary_methods %}
      ~{{ name }}.{{ item }}
   {%- endfor %}
   {% endif %}
   {% endblock %}

   {% block attributes %}
   {% if summary_attributes %}
   .. rubric:: {{ _('Attributes') }}

   .. autosummary::
   {% for item in summary_attributes %}
      ~{{ name }}.{{ item }}
   {%- endfor %}
   {% endif %}
   {% endblock %}
