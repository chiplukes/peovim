; Base C highlighting applies to most C++ syntax too.

(identifier) @variable

((identifier) @constant
 (#match? @constant "^[A-Z][A-Z\\d_]*$"))

[
 "break"
 "case"
 "const"
 "continue"
 "default"
 "do"
 "else"
 "enum"
 "extern"
 "for"
 "if"
 "inline"
 "return"
 "sizeof"
 "static"
 "struct"
 "switch"
 "typedef"
 "union"
 "volatile"
 "while"
 "#define"
 "#elif"
 "#else"
 "#endif"
 "#if"
 "#ifdef"
 "#ifndef"
 "#include"
 "catch"
 "class"
 "co_await"
 "co_return"
 "co_yield"
 "constexpr"
 "constinit"
 "consteval"
 "delete"
 "explicit"
 "final"
 "friend"
 "mutable"
 "namespace"
 "noexcept"
 "new"
 "override"
 "private"
 "protected"
 "public"
 "template"
 "throw"
 "try"
 "typename"
 "using"
 "concept"
 "requires"
 "virtual"
] @keyword

(preproc_directive) @keyword

[
 "--"
 "-"
 "-="
 "->"
 "="
 "!="
 "*"
 "&"
 "&&"
 "+"
 "++"
 "+="
 "<"
 "=="
 ">"
 "||"
] @operator

[
 "."
 ";"
] @delimiter

(string_literal) @string
(system_lib_string) @string
(raw_string_literal) @string
(null) @constant
"nullptr" @constant
(number_literal) @number
(char_literal) @number

(field_identifier) @property
(statement_identifier) @label
(type_identifier) @type
(primitive_type) @type
(sized_type_specifier) @type

((namespace_identifier) @type
 (#match? @type "^[A-Z]"))

(auto) @type

(call_expression
  function: (identifier) @function)

(call_expression
  function: (field_expression
    field: (field_identifier) @function))

(call_expression
  function: (qualified_identifier
    name: (identifier) @function))

(template_function
  name: (identifier) @function)

(template_method
  name: (field_identifier) @function)

(function_declarator
  declarator: (identifier) @function)

(function_declarator
  declarator: (field_identifier) @function)

(function_declarator
  declarator: (qualified_identifier
    name: (identifier) @function))

(preproc_function_def
  name: (identifier) @function.macro)

(comment) @comment
