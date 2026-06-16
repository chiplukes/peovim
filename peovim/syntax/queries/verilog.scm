; Generic fallback first so later, more specific captures can override it.
(simple_identifier) @variable

(comment) @comment

(integral_number) @number

[
  "["
  "]"
  "("
  ")"
  "{"
  "}"
] @punctuation.bracket

[
  "begin"
  "end"
] @keyword.control

[
  "if"
  "else"
] @keyword.conditional

[
  (module_keyword)
  (edge_identifier)
] @keyword

(always_keyword) @keyword.control

(port_direction) @type.builtin
(net_type) @type.builtin
(integer_vector_type) @type.builtin

(module_header
  (simple_identifier) @module)

(function_identifier) @function

(task_identifier) @function

(name_of_instance) @module.instance

(parameter_identifier) @parameter

(port_identifier) @parameter

(named_port_connection) @field

(system_tf_identifier) @function.builtin

(text_macro_identifier) @constant.macro