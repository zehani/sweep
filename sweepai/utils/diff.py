import difflib
import re

def diff_contains_dups_or_removals(diff, new_code):
    # The regex pattern for lines removed or added in the actual code
    removed_line_pattern = r"^-.*"
    added_line_pattern = r"^\+.*"

    lines_removed = False
    duplicate_lines_added = False

    # Split the diff and new_code into separate lines
    diff_lines = diff.split('\n')[3:]  # Start from the third line
    new_code_lines = [line.strip() for line in new_code.split('\n')]

    # Check if there are removed lines
    for line in diff_lines:
        if re.match(removed_line_pattern, line):
            lines_removed = True

    # Check if there are duplicate lines added
    added_lines = [line[1:].strip() for line in diff_lines if re.match(added_line_pattern, line)]
    for line in added_lines:
        if new_code_lines.count(line) > 1:
            duplicate_lines_added = True
            break

    return lines_removed or duplicate_lines_added

def generate_diff(old_code, new_code):
    old_code = old_code.strip()
    new_code = new_code.strip()
    
    diff = difflib.unified_diff(
        old_code.splitlines(keepends=True),
        new_code.splitlines(keepends=True)
    )

    diff_text = ''.join(diff)
    
    return diff_text

def revert_whitespace_changes(original_file_str, modified_file_str):
    original_lines = original_file_str.splitlines()
    modified_lines = modified_file_str.splitlines()

    diff = difflib.SequenceMatcher(None, original_lines, modified_lines)

    final_lines = []
    for opcode in diff.get_opcodes():
        if opcode[0] == "equal" or opcode[0] == "replace":
            # If the lines are equal or replace (means the change is not whitespace only)
            # use original lines.
            final_lines.extend(original_lines[opcode[1]:opcode[2]])
        elif opcode[0] == "insert":
            # If the lines are inserted in the modified file, check if it's just whitespace changes
            # If it's just whitespace changes, ignore them.
            for line in modified_lines[opcode[3]:opcode[4]]:
                if line.strip() != "":
                    final_lines.append(line)

    return '\n'.join(final_lines)


def format_contents(file_contents, is_markdown=False):
    """
    Add arbitrary postprocessing here, this affects files and diffs
    """
    lines = file_contents.split('\n')

    if is_markdown:
        return '\n'.join(lines)

    # Handle small files
    if len(lines) <= 5:
        start_idx = 0
        end_idx = len(lines)
        for idx, line in enumerate(lines):
            if start_idx == 0 and line.strip().startswith('```'):
                start_idx = idx + 1
            if start_idx != 0 and line.strip().endswith('```'):
                end_idx = idx
        lines = lines[start_idx:end_idx]
        return '\n'.join(lines)

    first_three_lines = lines[:3]
    last_three_lines = lines[-3:]
    first_line_idx = 0
    last_line_idx = 3
    for idx, line in enumerate(first_three_lines):
        line = line.strip()
        if line.startswith('```'):
            first_line_idx = max(first_line_idx, idx + 1)
        if 'user_code>' in line:
            first_line_idx = max(first_line_idx, idx + 1)
    for idx, line in enumerate(last_three_lines): # Check in reverse
        line = line.strip()
        if line.endswith('```'):
            last_line_idx = min(idx, last_line_idx)
        if 'user_code>' in line:
            last_line_idx = min(idx, last_line_idx)
    first_three_lines = first_three_lines[first_line_idx:]
    last_three_lines = last_three_lines[:last_line_idx]

    lines = first_three_lines + lines[3:-3] + last_three_lines
    return '\n'.join(lines)


def generate_new_file(modify_file_response: str, old_file_content: str, chunk_offset: int=0) -> str:
    result_file = ""
    old_file_lines = old_file_content.splitlines()

    # Extract content between <new_file> tags
    new_file = re.search(r".*?<new_file>\n?(.*)\n<\/new_file>", modify_file_response, re.DOTALL).group(1)
    if "<copy_lines" not in new_file:
        return new_file

    # v5
    result = []
    lines = new_file.split('\n')
    for line_number, line in enumerate(lines):
        # Todo: make it support 1 number only
        matches = re.finditer(r"<copy_lines\s(\d+-\d+)/?>", line)
        copied_lines = False
        for match in matches:
            copied_lines = True
            start, end = match.group(1).split('-')
            start, end = int(start) - 1, int(end) - 1
            if chunk_offset != 0: # Correct for the line numbers being much higher
                start -= chunk_offset
                end -= chunk_offset
            start = max(0, start)
            end = min(len(old_file_lines) - 1, end)

            replacements = old_file_lines[start:end + 1]
            replacements_str = '\n'.join(replacements)
            line = line.replace(match.group(0), replacements_str)

        # check if line was incorrectly duplicated
        append = True
        if not copied_lines:  # if bot generated, and line before is not bot generated
            if len(result) > 0:
                # Get last line in results
                last_group = result[-1]
                # last_line = last_group
                if '\n' in last_group:
                    last_line = last_group[last_group.rindex('\n') + 1:]  # if its multiple lines
                    # if last line is same is current line
                    if last_line == line:
                        append = False

        if append:
            result.append(line)
    result = '\n'.join(result)

    return result

NOT_FOUND = "NOT_FOUND"
IDENTICAL_LINES = "IDENTICAL_LINES"
MULTIPLE_HITS = "MULTIPLE_HITS"
def match_string(original, search, start_index=None):
    index = -1
    max_similarity = 0
    current_hits = 0
    # sliding window comparison from original to search
    # Todo: 2 pointer approach (find start, then find end)
    # Todo: use rapidfuzz to compute fuzzy similarity over code
    for i in range(start_index or 0, len(original)):
        count = 0
        for j in range(len(search)):
            if i + j < len(original) and search[j].strip() == original[i + j].strip():
                count += 1

                # If searching for previous snippet (like regex)
                if start_index is not None and search[j] == original[i + j]:
                    count += 0.01
        if count > max_similarity:
            index = i
            max_similarity = count
            current_hits = 1
        elif count == max_similarity:
            current_hits += 1
    return index, max_similarity, current_hits

def lstrip_max(s, chars, max_count):
    count = 0
    for char in s:
        if char in chars and count < max_count:
            count += 1
        else:
            break
    return s[count:]

def get_snippet_with_padding(original, index, search):
    snippet = original[index:index + len(search)]

    # Fix whitespace
    if len(search[0]) - len(search[0].lstrip()) == 0:
        spaces = ' ' * (len(snippet[0]) - len(snippet[0].lstrip()))
        strip = False
    else:  # Do diff between snippet and search
        # Todo(lukejagg): This might need to be more robust.

        # Check multiple lines for their whitespace
        min_whitespace = min([len(s) - len(s.lstrip()) for s in search])
        spaces = ' ' * min_whitespace
        strip = True

    return snippet, spaces, strip

def sliding_window_replacement(original, search, replace, search_context_before=None):
    status, replace_index = None, None
    # First, do check for "..." (example: define method, then put ... to ignore initial lines)
    canDoDotCheck = not any('...' in line.strip() for line in original)  # If ... not in original file
    if canDoDotCheck:
        # Check first 3 lines for '...'
        first_line_idx = -1
        for i in range(len(search)):
            if search[i].strip() == '...':
                first_line_idx = i
                break

        # Do this for replace too
        first_line_idx_replace = -1
        for i in range(len(replace)):
            if replace[i].strip() == '...':
                first_line_idx_replace = i
                break

        if first_line_idx == 0 and first_line_idx_replace == 0:
            search = search[1:]
            replace = replace[1:]
        elif first_line_idx == len(search) - 1 and first_line_idx_replace == len(replace) - 1:
            search = search[:first_line_idx]
            replace = replace[:first_line_idx_replace]
        elif first_line_idx != -1 and first_line_idx_replace != -1:
            # SPLIT INTO TWO PARTS
            # TODO(lukejagg): pass in the first and last lines as context for matching (so ambiguous ... can be matched)
            search_context_before = search[:first_line_idx]
            original, replace_index, status = sliding_window_replacement(original, search[first_line_idx + 1:], replace[first_line_idx_replace + 1:], search_context_before)
            search = search[:first_line_idx]
            replace = replace[:first_line_idx_replace]

    index, max_similarity, current_hits = match_string(original, search)

    # No changes could be found. Return original code.
    if max_similarity == 0:
        print('WARNING: No identical lines')
        return original, None, IDENTICAL_LINES
    if current_hits > 1:
        # First, try matching beginning of search
        success = False
        if search_context_before:
            old_index, _, current_hits = match_string(original, search_context_before)
            _, old_spaces, _ = get_snippet_with_padding(original, old_index, search_context_before)

            if current_hits == 1:
                index, max_similarity, current_hits = match_string(original, [old_spaces + s for s in search], start_index=old_index + 1)
                current_hits = 1  # Ignore multiple hits, use first complete comparison
                success = True

        if not success:
            print('WARNING: Multiple hits')
            return original, None, MULTIPLE_HITS
    if index == -1:
        return original, None, NOT_FOUND

    # if max_similarity != len(search):
    snippet, spaces, strip = get_snippet_with_padding(original, index, search)
    if strip:
        # Todo: What if whitespace in search is incorrect
        first_line_spaces = min([len(s) - len(s.lstrip()) for s in search])
        modified = [spaces + (lstrip_max(line, [' '], first_line_spaces) if strip else line) for line in replace]
    else:
        modified = [spaces + line for line in replace]

    # replaced original with modified
    original = original[:index] + modified + original[index + len(search):]
    return original, index, None

def get_all_diffs(modify_file_response: str) -> str:
    matches = re.findall(r'(<<<<.*?\n(.*?)\n====[^\n=]*\n(.*?)\n?>>>>)', modify_file_response, re.DOTALL)
    result = "\n\n".join([_match for _match, *_ in matches])
    return result

def generate_new_file_from_patch(modify_file_response: str, old_file_content: str, chunk_offset: int=0) -> str:
    old_file_lines = old_file_content.splitlines()
    
    # Extract content between <new_file> tags
    matches = re.findall(r'<<<<.*?\n(.*?)\n====[^\n=]*\n(.*?)\n?>>>>', modify_file_response, re.DOTALL)

    if not old_file_content.strip():
        # If old file is empty, just return the first match
        print(matches)
        search_and_replace, *_ = matches
        return search_and_replace[1]

    for search, replace in matches:
        # Remove trailing tags
        if search.lstrip().startswith('<old_file>') and replace.lstrip().startswith('<old_file'):
            search = search.lstrip()[len('<old_file>'):]
            replace = replace.lstrip()[len('<old_file>'):]
        # Remove trailing tags
        if search.rstrip().endswith('</old_file>') and replace.rstrip().endswith('</old_file'):
            search = search.rstrip()[:-len('</old_file>')]
            replace = replace.rstrip()[:-len('</old_file>')]

        old_file_lines, replace_index, status = sliding_window_replacement(old_file_lines, search.splitlines(), replace.splitlines())

    result = '\n'.join(old_file_lines)
    return result


def join_contents_k(first, second, k):
    """
    Join contents together removing k duplicate lines
    """
    first_lines = first.splitlines()
    second_lines = second.splitlines()
    for i in range(k, 0, -1):
        if len(first_lines) < k or len(second_lines) < k:
            continue
        if first_lines[-i:] == second_lines[:i]:
            return "\n".join(first_lines) + "\n" + "\n".join(second_lines[i:])
    return "\n".join(first_lines) + "\n" + "\n".join(second_lines)


def is_markdown(filename):
    return filename.endswith(".md") or filename.endswith(".rst") or filename.endswith(".txt")
