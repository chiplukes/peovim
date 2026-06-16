; Core C highlighting

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
(null) @constant
(number_literal) @number
(char_literal) @number

(field_identifier) @property
(statement_identifier) @label
(type_identifier) @type
(primitive_type) @type
(sized_type_specifier) @type

(call_expression
  function: (identifier) @function)

(call_expression
  function: (field_expression
    field: (field_identifier) @function))

(function_declarator
  declarator: (identifier) @function)

(preproc_function_def
  name: (identifier) @function.macro)

(comment) @comment
