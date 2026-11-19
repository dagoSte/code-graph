"""Study-only structural test retention, with independent frozen-test scoring."""
import ast
from collections import Counter
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('study_real_pilot', Path(__file__).with_name('real_pilot.py'))
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)

CONTAINERS = {'block', 'statement_block', 'class_body', 'interface_body',
              'enum_body', 'switch_body', 'statement_list', 'declaration_list'}


def relevant(node):
    return node.type.endswith(('statement', 'declaration', 'case', 'clause')) or node.type in {
        'method_definition', 'arrow_function', 'function_expression', 'method_signature',
        'function_signature', 'comment'}


def counts(source, language, filename=''):
    if language == 'python':
        return pilot.statement_counts(ast.parse(source))
    if language not in ('typescript', 'go'):
        raise ValueError('Unsupported study language')
    from tree_sitter_language_pack import get_parser
    data = source.encode('utf-8')
    parser_name = 'tsx' if language == 'typescript' and filename.endswith('.tsx') else language
    root = get_parser(parser_name).parse(data).root_node
    if root.has_error:
        raise ValueError('Changed test has partial syntax')

    def header(node):
        if not node.children:
            return node.type, data[node.start_byte:node.end_byte]
        return node.type, tuple((child.type,) if child.type in CONTAINERS else header(child)
                               for child in node.children
                               if not (child.is_named and relevant(child)))

    result, stack = Counter(), [root]
    while stack:
        node = stack.pop()
        if relevant(node):
            result[header(node)] += 1
        stack.extend(node.named_children)
    return result


def retained(candidate, baseline, test_files, language):
    """Existing statement/header counts cannot fall; valid nested additions may grow."""
    missing = []
    for name in test_files:
        old, new = baseline / name, candidate / name
        if not old.is_file():
            continue
        try:
            before = old.read_text(encoding='utf-8')
            after = new.read_text(encoding='utf-8')
            if before != after and counts(before, language, name) - counts(after, language, name):
                missing.append(name)
        except (OSError, UnicodeError, SyntaxError, ValueError):
            missing.append(name)
    return dict(tests_preserved=not missing, weakened_test_ids=missing,
                guard='statement_multiset_and_headers', semantic_equivalence_proven=False)
