import ast
import os
import glob

# --- CONFIGURATION ---
DIRECTORY_TO_SCAN = "."  # "." means current directory. Change to your path if needed.
OUTPUT_FILE = "pipeline_skeleton.md"
INCLUDE_FIRST_LINE_DOCSTRING = True # Set to False for maximum token savings

def get_first_line_of_docstring(node):
    """Extracts just the first line of a docstring to save space."""
    if not INCLUDE_FIRST_LINE_DOCSTRING:
        return None
    docstring = ast.get_docstring(node)
    if docstring:
        return docstring.strip().split('\n')[0]
    return None

def format_arguments(args_node):
    """Extracts function/method arguments into a clean string."""
    args = [arg.arg for arg in args_node.args]
    # Add *args and **kwargs if they exist
    if args_node.vararg:
        args.append(f"*{args_node.vararg.arg}")
    if args_node.kwarg:
        args.append(f"**{args_node.kwarg.arg}")
    return ", ".join(args)

def parse_file(filepath):
    """Reads a Python file and returns its skeleton as a list of strings."""
    with open(filepath, 'r', encoding='utf-8') as f:
        try:
            tree = ast.parse(f.read(), filename=filepath)
        except SyntaxError:
            return ["*Syntax error in file, could not parse.*"]

    lines = []
    
    for node in tree.body:
        # Handle Classes
        if isinstance(node, ast.ClassDef):
            bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
            base_str = f"({', '.join(bases)})" if bases else ""
            lines.append(f"class {node.name}{base_str}:")
            
            doc = get_first_line_of_docstring(node)
            if doc: lines.append(f'    """{doc}..."""')

            has_methods = False
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    has_methods = True
                    arg_str = format_arguments(child.args)
                    lines.append(f"    def {child.name}({arg_str}):")
                    method_doc = get_first_line_of_docstring(child)
                    if method_doc:
                        lines.append(f'        """{method_doc}..."""')
                    else:
                        lines.append("        pass")
            
            if not has_methods and not doc:
                lines.append("    pass")
            lines.append("") # Empty line for spacing

        # Handle Standalone Functions
        elif isinstance(node, ast.FunctionDef):
            arg_str = format_arguments(node.args)
            lines.append(f"def {node.name}({arg_str}):")
            doc = get_first_line_of_docstring(node)
            if doc:
                lines.append(f'    """{doc}..."""')
            else:
                lines.append("    pass")
            lines.append("")

    return lines

def generate_map():
    print(f"Scanning directory: {os.path.abspath(DIRECTORY_TO_SCAN)}")
    py_files = glob.glob(os.path.join(DIRECTORY_TO_SCAN, "*.py"))
    
    # Exclude the mapper script itself if it's in the same folder
    py_files = [f for f in py_files if os.path.basename(f) != os.path.basename(__file__)]
    
    if not py_files:
        print("No Python files found in this directory.")
        return

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as out:
        out.write("# Blender Pipeline Skeleton\n\n")
        out.write("> This file contains the architectural map of the codebase. Logic has been stripped to save context tokens.\n\n")
        
        for filepath in sorted(py_files):
            filename = os.path.basename(filepath)
            print(f"Mapping: {filename}...")
            
            out.write(f"## File: `{filename}`\n")
            out.write("```python\n")
            
            skeleton_lines = parse_file(filepath)
            if skeleton_lines:
                out.write("\n".join(skeleton_lines))
            else:
                out.write("# (No classes or functions defined in root)\n")
                
            out.write("```\n\n")
            
    print(f"\nDone! Skeleton saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    generate_map()