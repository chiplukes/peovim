; XDC / SDC constraint file highlights
; Reuses the bash tree-sitter grammar (XDC is Tcl-based; bash parses it
; well enough for tokenisation purposes).
;
; NOTE: [get_ports clk] is parsed by the bash grammar as two concatenation
; nodes ([get_ports and clk]) rather than a command substitution, so getter
; functions need two separate capture rules: one for standalone calls and one
; for the word node inside the concatenation that follows "[".

(comment) @comment

; Timing / placement constraint commands (as top-level command names)
(
  (command_name) @keyword
  (#match? @keyword "^(set_property|set_false_path|set_multicycle_path|set_input_delay|set_output_delay|set_clock_uncertainty|set_clock_latency|set_clock_groups|set_max_delay|set_min_delay|set_timing_derate|set_load|set_switching_activity|set_data_check|set_disable_timing|create_clock|create_generated_clock|current_design|read_xdc|source)$")
)

; Collection getter / helper commands as standalone calls (uncommon in XDC but valid)
(
  (command_name) @function
  (#match? @function "^(get_ports|get_nets|get_cells|get_clocks|get_pins|get_sites|get_tiles|get_bel_pins|get_property|get_pblocks|get_iobanks|get_slrs|all_inputs|all_outputs|all_clocks|all_registers|all_dsps|all_rams|filter|add_cells_to_pblock|resize_pblock|create_pblock|delete_pblock)$")
)

; Collection getters inside [bracket substitution] — the bash grammar splits
; [get_ports clk] into concatenation nodes, so we match the word directly.
(
  (concatenation (word) @function)
  (#match? @function "^(get_ports|get_nets|get_cells|get_clocks|get_pins|get_sites|get_tiles|get_bel_pins|get_property|get_pblocks|get_iobanks|get_slrs|all_inputs|all_outputs|all_clocks|all_registers|all_dsps|all_rams|filter|add_cells_to_pblock|resize_pblock|create_pblock|delete_pblock)$")
)

; Flags / options (words that start with -)
(
  (command (_) @variable.parameter)
  (#match? @variable.parameter "^-[a-zA-Z_]")
)

; Numeric literals (integers and floats, positive)
(
  (command (_) @number)
  (#match? @number "^[0-9]+(\.[0-9]+)?$")
)

; Quoted strings
[
  (string)
  (raw_string)
] @string
