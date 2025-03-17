## `pbg`: Package for translating Vivarium "1.0" `Process` implementations into `process-bigraph`/`bigraph-schema`-compliant interfaces

### Using the CLI application (using the demo example):

```bash
input_file="pbg/test_input/toy.py"
output_file="pbg/test_output/toy_edited.py"
translate process "$input_file" "$output_file"
```