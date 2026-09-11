from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET
from datetime import datetime


def escape_with_lb(text):
    """Escape text for XML, rendering each hard return as an explicit
    <lb/> element instead of a literal newline character."""
    return '<lb/>'.join(escape(part) for part in text.split('\n'))


def convert_ses_to_tei(ses_bytes, author, title, session, version, output_filename):
    """Replays a Schnappi Editor .ses keystroke-logging session export and
    reconstructs it as TEI-XML genetic-criticism markup: nested
    <add>/<del>/<mod> elements showing how the final text was built up and
    revised, keystroke by keystroke, including corrections and bulk
    (selection) deletions.

    The .ses format is much simpler than the GGXLog TSV format it is
    modelled after: every event is one of
      'ins'  - one or more characters inserted at <pos>
      'back' - a single character removed at <pos> (a BACK_SPACE press)
      'del'  - a whole span of text (<text>, possibly many characters)
               removed starting at <pos> in one action (a selection that
               was deleted or overwritten)
    and events are already given in chronological order via their id."""
    content = ses_bytes.decode('utf-8', errors='replace')
    root = ET.fromstring(content)

    # The .ses exporter appears to represent hard returns as the literal
    # character reference "&#xa;", but then escapes the "&" a second time
    # when writing the file, so the XML parser sees it as literal text
    # "&#xa;" rather than an actual newline. Undo that here.
    def fix_newlines(s):
        return s.replace('&#xa;', '\n')

    def field(tag, default=''):
        el = root.find(tag)
        if el is None or el.text is None:
            return default
        return fix_newlines(el.text)

    proj_name = field('proj_name')
    user_name = field('user_name')
    start_date_raw = field('start_date')  # e.g. "27.01.2026 17:36:37"
    start_text = field('start_text')

    # If the person didn't fill in Author/Title on upload, fall back to
    # what the .ses file itself records.
    author = author.strip() if author else ''
    title = title.strip() if title else ''
    if not author:
        author = user_name
    if not title:
        title = proj_name

    event_log = root.find('event_log')
    events = list(event_log) if event_log is not None else []

    def event_sort_key(el):
        try:
            return int(el.get('id'))
        except (TypeError, ValueError):
            return 0

    events.sort(key=event_sort_key)

    def clock_from_time(time_str, fallback):
        """Extracts HH:MM:SS from a .ses <time> value (e.g.
        "27.01.2026 17:36:37:557881") and strips the colons, matching the
        seq convention used for the GGXLog TSV format's Start_clock (e.g.
        "173637"). Falls back to `fallback` if the field is missing or
        doesn't look as expected."""
        if not time_str:
            return fallback
        parts = time_str.split(' ')
        if len(parts) < 2:
            return fallback
        clock_parts = parts[1].split(':')
        if len(clock_parts) < 3:
            return fallback
        return ''.join(clock_parts[:3])

    relevant_events = []
    for el in events:
        etype = el.get('type')
        if etype not in ('ins', 'del', 'back'):
            continue
        pos_el = el.find('pos')
        text_el = el.find('text')
        time_el = el.find('time')
        event_id = el.get('id')
        time_str = time_el.text if time_el is not None else ''
        relevant_events.append({
            'type': etype,
            'pos': int(pos_el.text) if pos_el is not None and pos_el.text is not None else 0,
            'text': fix_newlines(text_el.text) if text_el is not None and text_el.text is not None else '',
            'time': time_str,
            'seq': clock_from_time(time_str, event_id),
            'id': event_id,
        })

    class Token:
        """A node in the document tree. kind is one of:
        'add'  - inserted text (type_ is 'nt', 'context', 'continue',
                 'paste', or a '|'-joined combination such as
                 'nt|continue').
        'mod'  - resumption of a sentence left open elsewhere in the
                 document (type_ is always 'continue', optionally
                 combined, e.g. 'continue|paste').
        'del'  - removed text (type_ is 'context', 'pre-context', or a
                 combination such as 'context|continue').
        'text' - a plain, unannotated span (rarely used directly; most
                 leaf text is stored as bare Python strings instead).
        text is a list whose items are either bare strings or nested
        Token objects, so structure can be arbitrarily deep (e.g. a
        deletion nested inside an addition nested inside a deletion)."""

        def __init__(self, kind, text, seq=None, id_=None, type_=None):
            self.kind = kind
            self.text = text
            self.seq = seq
            self.id_ = id_
            self.type_ = type_

        def visible_length(self):
            """Number of characters this token contributes to the
            reconstructed, currently-visible document. Deletions always
            contribute 0, since their content is no longer present."""
            if self.kind == 'del':
                return 0
            if self.kind in ('add', 'mod'):
                return sum(
                    t.visible_length() if isinstance(t, Token) else len(t)
                    for t in self.text
                )
            if self.kind == 'text':
                return len(self.text)
            return len(self.text)

        def to_xml(self):
            """Renders this token (and everything nested inside it) as an
            XML string."""
            if self.kind == 'text':
                return escape_with_lb(self.text)
            inner = ""
            for t in self.text:
                if isinstance(t, Token):
                    inner += t.to_xml()
                else:
                    inner += escape_with_lb(t)
            if self.kind == 'add':
                return (
                    '<add seq="' + str(self.seq) +
                    '" type="' + str(self.type_) +
                    '" evidence="' + str(self.id_) +
                    '">' + inner + '</add>'
                )
            if self.kind == 'mod':
                return (
                    '<mod seq="' + str(self.seq) +
                    '" type="' + str(self.type_) +
                    '" evidence="' + str(self.id_) +
                    '">' + inner + '</mod>'
                )
            if self.kind == 'del':
                return (
                    '<del seq="' + str(self.seq) +
                    '" type="' + str(self.type_) +
                    '" evidence="' + str(self.id_) +
                    '">' + inner + '</del>'
                )
            return inner

    def visible_len(item):
        """Visible-character count of a single tree item, whether it's a
        bare string or a Token."""
        if isinstance(item, Token):
            return item.visible_length()
        return len(item)

    def sequence_visible_length(items):
        """Total visible-character count of a list of tree items."""
        return sum(visible_len(item) for item in items)

    SENTENCE_END_CHARS = ('.', '!', '?', '\n')

    def get_visible_text(items):
        """Reconstructs the currently-visible text of a list of tree
        items, skipping the contents of any 'del' tokens."""
        parts = []
        for item in items:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Token):
                if item.kind == 'del':
                    continue
                elif item.kind in ('add', 'mod'):
                    parts.append(get_visible_text(item.text))
                elif item.kind == 'text':
                    parts.append(item.text)
        return ''.join(parts)

    # Tracks add/mod Token objects (at any nesting depth) representing a
    # sentence that hasn't yet been terminated with . ! ? or a newline.
    # Consulted by classify_add_type to recognise when new typing is
    # resuming an unfinished sentence rather than starting a fresh one.
    open_sentences = set()

    def ends_with_terminator(items):
        """True if the visible text of `items`, ignoring trailing
        whitespace, ends in a sentence-terminating character."""
        text = get_visible_text(items).rstrip()
        return bool(text) and text[-1] in SENTENCE_END_CHARS

    def find_leaf_owner(tokens, pos):
        """Returns the innermost add/mod Token that owns the visible
        character at `pos`, descending through any nesting. Returns None
        if `pos` doesn't land inside any add/mod token (e.g. it's at or
        past the end of everything)."""
        idx, offset = get_visible_index(tokens, pos)
        if idx >= len(tokens):
            return None
        target = tokens[idx]
        if isinstance(target, Token) and target.kind in ('add', 'mod'):
            deeper = find_leaf_owner(target.text, offset)
            return deeper if deeper is not None else target
        return None

    def classify_add_type(tokens, pos):
        """Classifies an insertion at visible position `pos` as one of:
        'nt'       - nothing (non-terminated) precedes it, so it starts a
                     brand-new sentence.
        'continue' - it sits exactly at the growing edge of a sentence
                     that is still open (not yet terminated), meaning the
                     writer left that sentence unfinished, did something
                     else, and has now come back to carry on with it.
                     This holds even if unrelated text already sits after
                     the insertion point, since that text was written
                     separately, out of order.
        'context'  - existing content precedes it, but it isn't at an
                     open sentence's growing edge: a revision inserted
                     within content that's already there on both sides."""
        if pos <= 0:
            return 'nt'
        owner_before = find_leaf_owner(tokens, pos - 1)
        if owner_before is not None and owner_before in open_sentences:
            owner_at = find_leaf_owner(tokens, pos)
            if owner_at is not owner_before:
                return 'continue'
            return 'context'
        prefix = get_visible_text(tokens)[:pos].rstrip()
        if not prefix or prefix[-1] in SENTENCE_END_CHARS:
            return 'nt'
        return 'context'

    def compact_items(items):
        """Cleans up a list of tree items: merges adjacent bare strings,
        drops empty/falsy entries, and recursively compacts the contents
        of any nested add/mod tokens."""
        compacted = []
        for item in items:
            if item is None:
                continue
            if isinstance(item, str):
                if not item:
                    continue
                if compacted and isinstance(compacted[-1], str):
                    compacted[-1] += item
                else:
                    compacted.append(item)
                continue
            if isinstance(item, Token):
                if item.kind in ('add', 'mod'):
                    item.text = compact_items(item.text)
                    if not item.text:
                        continue
                elif item.kind == 'text':
                    if not item.text:
                        continue
                elif item.kind == 'del':
                    if not item.text:
                        continue
                compacted.append(item)
        return compacted

    def get_visible_index(tokens, pos):
        """Finds the top-level item in `tokens` that contains visible
        position `pos`, returning (item_index, offset_within_item). If
        `pos` is at or past the end of everything, returns
        (len(tokens), 0)."""
        visible_pos = 0
        for i, token in enumerate(tokens):
            length = visible_len(token)
            if visible_pos + length > pos:
                return i, pos - visible_pos
            visible_pos += length
        return len(tokens), 0

    def pop_leading_dels(tokens, pos):
        """Zero-length del tokens are invisible, so position-based lookups
        like get_visible_index skip straight past them. If one is sitting
        structurally right at visible position `pos` (possibly nested
        inside an add/mod), this removes and returns it (and any other
        such dels immediately there, in order) so callers can carry it
        along instead of losing it when the surrounding text is edited."""
        collected = []
        visible_pos = 0
        idx = 0
        while idx < len(tokens):
            item = tokens[idx]
            length = visible_len(item)
            if isinstance(item, Token) and item.kind == 'del' and visible_pos == pos:
                collected.append(tokens.pop(idx))
                continue
            if visible_pos + length > pos:
                if isinstance(item, Token) and item.kind in ('add', 'mod'):
                    collected.extend(pop_leading_dels(item.text, pos - visible_pos))
                break
            visible_pos += length
            idx += 1
        return collected

    def pop_leading_zero_length(tokens, pos):
        """Like pop_leading_dels, but also sweeps up add/mod tokens whose
        own net visible contribution is zero (e.g. something typed and
        then immediately fully deleted again within the same still-open
        buffer), not just del tokens. Used when reconstructing a span
        that wholesale-absorbs everything sitting at a position,
        regardless of what kind of token happens to be there."""
        collected = []
        visible_pos = 0
        idx = 0
        while idx < len(tokens):
            item = tokens[idx]
            length = visible_len(item)
            if length == 0 and visible_pos == pos:
                collected.append(tokens.pop(idx))
                continue
            if visible_pos + length > pos:
                if isinstance(item, Token) and item.kind in ('add', 'mod'):
                    collected.extend(pop_leading_zero_length(item.text, pos - visible_pos))
                break
            visible_pos += length
            idx += 1
        return collected

    def extract_span_smart(tokens, start_pos, length):
        """Removes and returns a list of items covering `length` visible
        characters starting at visible position `start_pos` within
        `tokens`, mutating `tokens` in place. Unlike plain
        character-by-character extraction, this preserves structure: an
        add/mod token that falls entirely within the span is kept
        wholesale, with its own seq/id/type intact, since it was a
        genuine, separately-typed unit that never survived. A token the
        span only partially covers is split -- the surviving portion
        stays behind in `tokens` unchanged, and the consumed portion is
        extracted as plain content (recursing the same way, with no new
        wrapper of its own, since it isn't a separate loggable action).
        Zero-length tokens (already-invisible deletions, or additions
        that were themselves entirely undone) are carried along in their
        correct relative position via pop_leading_zero_length."""
        extracted = []
        remaining = length
        extracted.extend(pop_leading_zero_length(tokens, start_pos))
        idx, offset = get_visible_index(tokens, start_pos)
        while remaining > 0 and idx < len(tokens):
            item = tokens[idx]
            item_len = visible_len(item)
            if item_len == 0:
                idx += 1
                continue
            available = item_len - offset
            take = min(available, remaining)
            if offset == 0 and take == item_len:
                extracted.append(tokens.pop(idx))
                remaining -= take
                extracted.extend(pop_leading_zero_length(tokens, start_pos))
                idx, offset = get_visible_index(tokens, start_pos)
                continue
            if isinstance(item, str):
                local_start = offset
                local_end = offset + take
                before = item[:local_start]
                middle = item[local_start:local_end]
                after = item[local_end:]
                replacement = []
                if before:
                    replacement.append(before)
                if after:
                    replacement.append(after)
                tokens[idx:idx + 1] = replacement
                if middle:
                    extracted.append(middle)
                remaining -= take
                extracted.extend(pop_leading_zero_length(tokens, start_pos))
                idx, offset = get_visible_index(tokens, start_pos)
                continue
            if isinstance(item, Token) and item.kind in ('add', 'mod'):
                consumed = extract_span_smart(item.text, offset, take)
                extracted.extend(consumed)
                remaining -= take
                extracted.extend(pop_leading_zero_length(tokens, start_pos))
                idx, offset = get_visible_index(tokens, start_pos)
                continue
            if isinstance(item, Token) and item.kind == 'text':
                local_start = offset
                local_end = offset + take
                before = item.text[:local_start]
                middle = item.text[local_start:local_end]
                after = item.text[local_end:]
                replacement = []
                if before:
                    replacement.append(Token('text', before))
                if after:
                    replacement.append(Token('text', after))
                tokens[idx:idx + 1] = replacement
                if middle:
                    extracted.append(middle)
                remaining -= take
                extracted.extend(pop_leading_zero_length(tokens, start_pos))
                idx, offset = get_visible_index(tokens, start_pos)
                continue
            break
        return extracted

    def merge_adjacent_zero_length(items):
        """Combines any consecutive run of zero-visible-length items (del
        tokens, or add/mod tokens whose own net contribution is zero
        because everything they typed was itself later undone) into a
        single wrapper del, nesting each original item inside it as-is.
        This reflects that such a run represents one continuous stretch
        that was drafted and abandoned before the writer ever moved past
        it, rather than a set of unrelated, independently-settled
        deletions that merely happen to sit next to each other. Content
        with real visible length is left alone and breaks the run."""
        result = []
        run = []

        def flush_run():
            if not run:
                return
            if len(run) == 1:
                result.append(run[0])
                run.clear()
                return
            first = run[0]
            if isinstance(first, Token) and first.kind == 'del':
                # `first` is already a del: fold the rest of the run into
                # it directly rather than wrapping it in another del that
                # would carry the same evidence. Reclassify it as
                # pre-context, since as part of a merged group it now
                # represents content that was never truly settled.
                first.text = first.text + list(run[1:])
                first.type_ = 'pre-context'
                result.append(first)
            else:
                result.append(Token(
                    'del',
                    list(run),
                    seq=first.seq,
                    id_=first.id_,
                    type_='pre-context'
                ))
            run.clear()

        for item in items:
            length = visible_len(item)
            if length == 0 and isinstance(item, Token) and item.kind in ('del', 'add', 'mod'):
                run.append(item)
            else:
                flush_run()
                result.append(item)
        flush_run()
        return result

    def insert_token(tokens, token, pos):
        """Inserts `token` into `tokens` at visible position `pos`,
        splitting a bare string or a 'text' token in two around it if
        `pos` falls in the middle of one, or descending into an add/mod
        token if `pos` falls within its span. Falls back to a plain
        top-level insertion if nothing at `pos` can be split."""
        idx, offset = get_visible_index(tokens, pos)
        if idx >= len(tokens):
            tokens.append(token)
            return
        target = tokens[idx]
        if isinstance(target, str):
            before = target[:offset]
            after = target[offset:]
            replacement = []
            if before:
                replacement.append(before)
            replacement.append(token)
            if after:
                replacement.append(after)
            tokens[idx:idx + 1] = replacement
            return
        if isinstance(target, Token):
            if target.kind == 'text':
                before = target.text[:offset]
                after = target.text[offset:]
                replacement = []
                if before:
                    replacement.append(Token('text', before))
                replacement.append(token)
                if after:
                    replacement.append(Token('text', after))
                tokens[idx:idx + 1] = replacement
                return
            if target.kind in ('add', 'mod'):
                insert_token(target.text, token, offset)
                return
        tokens.insert(idx, token)

    def delete_char(tokens, pos, wrap_fn=None):
        """Removes the visible character at `pos`, returning it (or None
        if `pos` is out of range). If `wrap_fn` is given (a callable
        taking the deleted character and returning a Token to wrap it),
        the wrapper is spliced in atomically at the exact point the
        character occupied. This matters for two reasons: it keeps the
        new token in its true textual position relative to any trailing
        zero-length siblings that already sit right after it (a separate
        position-based insert_token call could misplace it there, since
        by the time that runs, the boundary it searches for has already
        shifted from removing the character); and building the wrapper
        via a callback, rather than passing in an empty pre-built Token,
        ensures the wrapper already holds its content by the time this
        recursion unwinds -- an ancestor add/mod's own compact_items()
        pass would otherwise see it while still empty and drop it."""
        if pos < 0:
            return None
        idx, offset = get_visible_index(tokens, pos)
        if idx >= len(tokens):
            return None
        target = tokens[idx]
        if isinstance(target, str):
            if offset < len(target):
                deleted_char = target[offset]
                before = target[:offset]
                after = target[offset + 1:]
                replacement = []
                if before:
                    replacement.append(before)
                if wrap_fn is not None:
                    replacement.append(wrap_fn(deleted_char))
                if after:
                    replacement.append(after)
                tokens[idx:idx + 1] = replacement
                return deleted_char
        elif isinstance(target, Token):
            if target.kind == 'text':
                if offset < len(target.text):
                    deleted_char = target.text[offset]
                    if wrap_fn is not None:
                        before = target.text[:offset]
                        after = target.text[offset + 1:]
                        replacement = []
                        if before:
                            replacement.append(Token('text', before))
                        replacement.append(wrap_fn(deleted_char))
                        if after:
                            replacement.append(Token('text', after))
                        tokens[idx:idx + 1] = replacement
                    else:
                        target.text = target.text[:offset] + target.text[offset + 1:]
                        if not target.text:
                            tokens.pop(idx)
                    return deleted_char
            elif target.kind in ('add', 'mod'):
                deleted_char = delete_char(target.text, offset, wrap_fn)
                target.text = compact_items(target.text)
                if not target.text:
                    tokens.pop(idx)
                return deleted_char
        return None

    def pop_last_visible_char(items):
        """Removes and returns the last visible character from `items`
        (scanning from the end, skipping over del tokens and descending
        into add/mod tokens as needed). Used for pre-context deletions,
        which always remove the tail of the still-open typing buffer."""
        for i in range(len(items) - 1, -1, -1):
            item = items[i]
            if isinstance(item, Token) and item.kind == 'del':
                continue
            if isinstance(item, Token) and item.kind in ('add', 'mod'):
                deleted_char = pop_last_visible_char(item.text)
                if deleted_char is not None:
                    item.text = compact_items(item.text)
                    if not item.text:
                        items.pop(i)
                    return deleted_char
                continue
            if isinstance(item, str):
                if not item:
                    continue
                deleted_char = item[-1]
                if len(item) == 1:
                    items.pop(i)
                else:
                    items[i] = item[:-1]
                return deleted_char
        return None

    # The finished document tree (top-level list of Token objects). If the
    # session began from a non-empty <start_text> (e.g. this is a
    # revision pass over a previous draft rather than writing from
    # scratch), that text is seeded here as a plain, unmarked span -- the
    # base layer everything else in the log edits. It is rendered as
    # ordinary running text; only what the log actually adds or removes
    # is wrapped in <add>/<del>.
    markup_tokens = [start_text] if start_text else []

    # The characters typed so far in the current, not-yet-settled run of
    # typing (a list of bare strings / nested Tokens, mirroring
    # markup_tokens' own item format). Flushed into markup_tokens as one
    # add/mod Token once the writer moves elsewhere or the run ends.
    current_add = []
    current_add_pos = None
    current_add_seq = None
    current_add_id = None

    # True if the run about to be flushed is a direct continuation of an
    # interrupted add/mod/del (see flush_current_add and the "|continue"
    # type suffix), with current_add_inherited_type holding the base type
    # ('nt' / 'context' / 'continue') it should inherit rather than being
    # freshly classified.
    current_add_resumes_after_del = False
    current_add_inherited_type = None

    # A selection (mouse click-drag or double-click) waiting to be
    # consumed by whatever keyboard action follows it.
    pending_selection = None

    # (position, base_type) recorded when a "regular" deletion removes
    # the trailing edge of an existing token; if typing resumes at that
    # exact position next, it inherits base_type + "|continue" instead of
    # being freshly classified from its new surroundings.
    pending_inherit_after_regular_delete = None

    # True if the character just appended to current_add was a sentence
    # terminator, so that a following space closes out the run instead of
    # extending it into the next sentence.
    sentence_end_pending = False

    # True right after a pre-context deletion succeeds; the next typed
    # character triggers a flush-and-restart of current_add (see the main
    # loop) so the resumed typing can be tagged "|continue".
    pending_flush_after_pre_context_del = False

    # The most recently created "context" del Token (and the position its
    # removed content ended at), so consecutive single-character
    # backspaces reaching into already-settled text merge into one del
    # instead of each becoming a separate element.
    last_context_del = None
    last_context_del_end_pos = None

    def flush_current_add():
        """Settles the in-progress current_add buffer into markup_tokens
        as a single add or mod Token (mod specifically for a 'continue'
        classification), classifying it via classify_add_type unless it's
        resuming an interrupted run, in which case it inherits that run's
        base type with "|continue" appended. Returns the base type of
        whatever was just flushed ('nt' / 'context' / 'continue'), or
        None if the buffer was empty -- callers use this to pass the type
        along to typing that resumes immediately after an interruption."""
        nonlocal current_add, current_add_pos, current_add_seq, current_add_id
        nonlocal current_add_resumes_after_del, current_add_inherited_type
        flushed_base_type = None
        if current_add:
            cleaned = compact_items(current_add.copy())
            if cleaned:
                if current_add_resumes_after_del and current_add_inherited_type is not None:
                    add_type = current_add_inherited_type
                    final_type = add_type + '|continue'
                else:
                    add_type = classify_add_type(markup_tokens, current_add_pos)
                    final_type = add_type
                flushed_base_type = add_type
                if add_type == 'continue':
                    new_token = Token(
                        'mod',
                        cleaned,
                        seq=current_add_seq,
                        id_=current_add_id,
                        type_=final_type
                    )
                else:
                    new_token = Token(
                        'add',
                        cleaned,
                        seq=current_add_seq,
                        id_=current_add_id,
                        type_=final_type
                    )
                insert_token(markup_tokens, new_token, current_add_pos)
                if add_type in ('nt', 'continue') and not ends_with_terminator(cleaned):
                    open_sentences.add(new_token)
        current_add = []
        current_add_pos = None
        current_add_seq = None
        current_add_id = None
        current_add_resumes_after_del = False
        current_add_inherited_type = None
        return flushed_base_type

    def insert_special_addition(text, pos, seq, id_, extra_type):
        """Inserts an addition that arrives as one complete, atomic chunk
        rather than being built up character-by-character (currently used
        for pastes). Still runs the insertion point through the normal
        nt/context/continue classification, but tags the result with a
        fixed type (e.g. 'paste') so these stay distinguishable from
        ordinary typed additions."""
        nonlocal pending_inherit_after_regular_delete
        if not text:
            return
        flush_current_add()
        pending_inherit_after_regular_delete = None
        base_type = classify_add_type(markup_tokens, pos)
        if base_type == 'continue':
            new_token = Token('mod', [text], seq=seq, id_=id_, type_=extra_type)
        else:
            new_token = Token('add', [text], seq=seq, id_=id_, type_=extra_type)
        insert_token(markup_tokens, new_token, pos)
        if base_type in ('nt', 'continue') and not ends_with_terminator([text]):
            open_sentences.add(new_token)

    def consume_selection(selected_text, start_pos, seq, id_):
        """Removes a span of text that was selected (via mouse click-drag
        or double-click) just before this keystroke event. Three cases:
        (1) the whole selection falls within text just typed and not yet
        flushed -- an immediate self-correction, extracted from
        current_add and tagged 'pre-context'; (2) the selection sits at
        the growing edge of an existing, still-open token -- extracted
        via extract_span_smart (preserving whole-token structure and
        splitting partially-consumed boundary tokens), merged where
        possible via merge_adjacent_zero_length, tagged 'pre-context', and
        nested into the token it extends; (3) otherwise, a plain
        character-by-character removal from already-settled text, tagged
        'context'."""
        nonlocal pending_flush_after_pre_context_del, last_context_del, last_context_del_end_pos
        nonlocal pending_inherit_after_regular_delete
        if not selected_text:
            return
        pending_inherit_after_regular_delete = None
        length = len(selected_text)
        is_pre_context = (
            current_add_pos is not None
            and bool(current_add)
            and start_pos >= current_add_pos
            and start_pos + length <= current_add_pos + sequence_visible_length(current_add)
        )
        if is_pre_context:
            rel_pos = start_pos - current_add_pos
            extracted = []
            for _ in range(length):
                extracted.extend(pop_leading_dels(current_add, rel_pos))
                ch = delete_char(current_add, rel_pos)
                if ch is None:
                    break
                extracted.append(ch)
            extracted = compact_items(extracted)
            if extracted:
                del_token = Token(
                    'del',
                    extracted,
                    seq=seq,
                    id_=id_,
                    type_='pre-context'
                )
                insert_token(current_add, del_token, rel_pos)
                pending_flush_after_pre_context_del = True
            return
        if current_add:
            flush_current_add()
        owner_before = find_leaf_owner(markup_tokens, start_pos - 1) if start_pos > 0 else None
        owner_after = find_leaf_owner(markup_tokens, start_pos + length)
        is_growing_edge = (owner_before is not None) and (owner_after is not owner_before)
        if is_growing_edge:
            extracted = extract_span_smart(markup_tokens, start_pos, length)
            extracted = compact_items(extracted)
            extracted = merge_adjacent_zero_length(extracted)
            if extracted:
                del_token = Token(
                    'del',
                    extracted,
                    seq=seq,
                    id_=id_,
                    type_='pre-context'
                )
                owner_before.text.append(del_token)
            last_context_del = None
            last_context_del_end_pos = None
            return
        extracted = []
        for _ in range(length):
            extracted.extend(pop_leading_dels(markup_tokens, start_pos))
            ch = delete_char(markup_tokens, start_pos)
            if ch is None:
                break
            extracted.append(ch)
        extracted = compact_items(extracted)
        if extracted:
            del_token = Token(
                'del',
                extracted,
                seq=seq,
                id_=id_,
                type_='context'
            )
            insert_token(markup_tokens, del_token, start_pos)
        last_context_del = None
        last_context_del_end_pos = None

    def handle_deletion(delete_pos, seq, id_, allow_pre_context=False):
        """Handles a single BACK_SPACE/DELETE keystroke at `delete_pos`.
        Four cases: (1) if `allow_pre_context` is set (a backspace) and
        the deletion lands on the tail of the still-open current_add
        buffer, the character is popped from the buffer itself and
        wrapped in a 'pre-context' del nested inside it (merging with an
        existing trailing pre-context del if there is one), and the
        buffer is flagged to flush-and-restart on the next typed
        character; (2) if it lands right where the previous 'context' del
        ended (one position before, since backspace's caret moves left
        each press), it merges into that same del, prepended, rather than
        creating a new one; (3) if it lands at exactly the same position
        the previous 'context' del ended at (DELETE's caret doesn't move,
        so the next character shifts into the same index each press), it
        likewise merges into that del, but appended, since the characters
        now arrive in left-to-right order; (4) otherwise it's a fresh
        'context' deletion from already-settled text, inserted atomically
        via delete_char's wrap_fn so it lands at its exact former textual
        position -- and if it happens to remove the trailing edge of the
        token that owns it, that token's base type is recorded so typing
        resuming at this position next inherits it (see
        pending_inherit_after_regular_delete and the main loop)."""
        nonlocal pending_flush_after_pre_context_del, last_context_del, last_context_del_end_pos
        nonlocal pending_inherit_after_regular_delete
        if delete_pos < 0:
            return
        current_visible_len = sequence_visible_length(current_add)
        if (
            allow_pre_context
            and current_add
            and current_add_pos is not None
            and current_visible_len > 0
            and delete_pos == current_add_pos + current_visible_len - 1
        ):
            deleted_char = pop_last_visible_char(current_add)
            if deleted_char is not None:
                last = current_add[-1] if current_add else None
                if (
                    isinstance(last, Token)
                    and last.kind == 'del'
                    and last.type_ == 'pre-context'
                ):
                    last.text.insert(0, deleted_char)
                else:
                    del_token = Token(
                        'del',
                        [deleted_char],
                        seq=seq,
                        id_=id_,
                        type_="pre-context"
                    )
                    current_add.append(del_token)
                pending_flush_after_pre_context_del = True
            last_context_del = None
            last_context_del_end_pos = None
            return
        if current_add:
            flush_current_add()
        pending_flush_after_pre_context_del = False
        if (
            last_context_del is not None
            and last_context_del_end_pos is not None
            and delete_pos == last_context_del_end_pos - 1
        ):
            pre_owner = find_leaf_owner(markup_tokens, delete_pos)
            is_trailing_edge = False
            if pre_owner is not None:
                next_owner = find_leaf_owner(markup_tokens, delete_pos + 1)
                is_trailing_edge = (next_owner is not pre_owner)
            deleted_char = delete_char(markup_tokens, delete_pos)
            if deleted_char is not None:
                last_context_del.text.insert(0, deleted_char)
                last_context_del_end_pos = delete_pos
                if is_trailing_edge and pre_owner is not None:
                    _base = pre_owner.type_.split('|')[0]
                    if _base in ('nt', 'context', 'continue'):
                        pending_inherit_after_regular_delete = (delete_pos, _base)
            return
        if (
            last_context_del is not None
            and last_context_del_end_pos is not None
            and delete_pos == last_context_del_end_pos
        ):
            # Forward-delete (DELETE key) continuation: the caret position
            # doesn't move -- the following character shifts left into the
            # same index on every repeated press. Chars therefore arrive in
            # left-to-right reading order, so append rather than prepend.
            # last_context_del_end_pos is left unchanged, since the position
            # this del "ends" at is still delete_pos.
            pre_owner = find_leaf_owner(markup_tokens, delete_pos)
            is_trailing_edge = False
            if pre_owner is not None:
                next_owner = find_leaf_owner(markup_tokens, delete_pos + 1)
                is_trailing_edge = (next_owner is not pre_owner)
            deleted_char = delete_char(markup_tokens, delete_pos)
            if deleted_char is not None:
                last_context_del.text.append(deleted_char)
                if is_trailing_edge and pre_owner is not None:
                    _base = pre_owner.type_.split('|')[0]
                    if _base in ('nt', 'context', 'continue'):
                        pending_inherit_after_regular_delete = (delete_pos, _base)
            return
        pre_owner = find_leaf_owner(markup_tokens, delete_pos)
        is_trailing_edge = False
        if pre_owner is not None:
            next_owner = find_leaf_owner(markup_tokens, delete_pos + 1)
            is_trailing_edge = (next_owner is not pre_owner)
        created = []

        def _wrap_context_del(ch):
            t = Token('del', [ch], seq=seq, id_=id_, type_="context")
            created.append(t)
            return t

        deleted_char = delete_char(markup_tokens, delete_pos, wrap_fn=_wrap_context_del)
        if deleted_char is not None:
            del_token = created[0]
            last_context_del = del_token
            last_context_del_end_pos = delete_pos
            if is_trailing_edge and pre_owner is not None:
                _base = pre_owner.type_.split('|')[0]
                if _base in ('nt', 'context', 'continue'):
                    pending_inherit_after_regular_delete = (delete_pos, _base)

    # Main replay loop: walks every 'ins' / 'back' / 'del' event in
    # chronological order (the .ses log is already ordered by id),
    # updating markup_tokens and current_add to reflect it. Unlike the
    # GGXLog TSV format, .ses events are self-contained -- a bulk 'del'
    # event already carries its own removed text, so there is no
    # separate selection row to buffer and no key-name-to-character
    # mapping to do (the logged text is always the literal character(s)).
    for ev in relevant_events:
        etype = ev['type']
        pos = ev['pos']
        seq = ev['seq']
        id_ = ev['id']

        if etype == 'del':
            # A whole selected span removed (or overwritten) in one
            # action. This is the bulk-deletion counterpart of
            # BACK_SPACE/DELETE consuming a mouse selection in the TSV
            # format, so it is handled the same way.
            consume_selection(ev['text'], pos, seq, id_)
            continue

        if etype == 'back':
            # A single BACK_SPACE press: removes the character sitting at
            # `pos`.
            handle_deletion(pos, seq, id_, allow_pre_context=True)
            continue

        # etype == 'ins'
        text = ev['text']
        if not text:
            continue
        if len(text) > 1:
            # A multi-character insertion arriving as one atomic chunk
            # (e.g. a paste) rather than being typed key by key.
            insert_special_addition(text, pos, seq, id_, 'paste')
            pending_flush_after_pre_context_del = False
            last_context_del = None
            last_context_del_end_pos = None
            continue

        char = text

        last_context_del = None
        last_context_del_end_pos = None

        if pending_flush_after_pre_context_del:
            # A pre-context deletion just fired; flush the interrupted
            # buffer and start a fresh one for this character, tagged as
            # a continuation of whatever was flushed.
            interrupted_type = flush_current_add()
            pending_flush_after_pre_context_del = False
            current_add_pos = pos
            current_add_seq = seq
            current_add_id = id_
            current_add_resumes_after_del = True
            current_add_inherited_type = interrupted_type
            pending_inherit_after_regular_delete = None
            sentence_end_pending = False
            current_add.append(char)
            if char in {'.', '!', '?', '\n'}:
                sentence_end_pending = True
            continue

        if current_add_pos is None or pos != current_add_pos + sequence_visible_length(current_add):
            # The caret jumped somewhere non-contiguous with the current
            # buffer: settle it and start a new one here. If this
            # position exactly matches where a trailing-edge regular
            # deletion just occurred, inherit its type as a continuation.
            flush_current_add()
            current_add_pos = pos
            current_add_seq = seq
            current_add_id = id_
            sentence_end_pending = False
            if pending_inherit_after_regular_delete is not None and pending_inherit_after_regular_delete[0] == pos:
                current_add_resumes_after_del = True
                current_add_inherited_type = pending_inherit_after_regular_delete[1]
            pending_inherit_after_regular_delete = None

        current_add.append(char)

        if sentence_end_pending and char == ' ':
            flush_current_add()
            sentence_end_pending = False
        elif char in {'.', '!', '?', '\n'}:
            sentence_end_pending = True
        else:
            sentence_end_pending = False

    flush_current_add()

    # start_date_raw looks like "27.01.2026 17:36:37"; the per-event
    # <time> field is the same but with an extra ":ffffff" microsecond
    # suffix, e.g. "27.01.2026 17:36:50:680333".
    date = ""
    time = ""
    session_duration = ""
    start_dt = None
    try:
        start_dt = datetime.strptime(start_date_raw, "%d.%m.%Y %H:%M:%S")
        date = start_dt.strftime("%d-%m-%Y")
        time = start_dt.strftime("%H:%M:%S")
    except ValueError:
        pass

    last_time_raw = relevant_events[-1]['time'] if relevant_events else ""
    if start_dt is not None and last_time_raw:
        try:
            end_dt = datetime.strptime(last_time_raw, "%d.%m.%Y %H:%M:%S:%f")
            session_duration = str(end_dt - start_dt).split('.')[0]
        except ValueError:
            session_duration = ""

    # Render the finished tree to XML.
    final_xml = ""
    for token in markup_tokens:
        if isinstance(token, Token):
            final_xml += token.to_xml()
        else:
            final_xml += escape_with_lb(token)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<TEI xmlns="http://www.tei-c.org/ns/1.0">',
        '    <teiHeader>',
        '        <fileDesc>',
        '            <titleStmt>',
        '                <title>' + escape(title) + '</title>',
        '                <author>' + escape(author) + '</author>',
        '            </titleStmt>',
        '            <publicationStmt>',
        '                <p corresp="session">' + escape(str(session)) + '</p>',
        '                <p corresp="version">' + escape(str(version)) + '</p>',
        '            </publicationStmt>',
        '            <sourceDesc>',
        '                <p>',
        '                    <l>' + escape(date) + '</l>',
        '                    <l>' + escape(time) + '</l>',
        '                    <l>' + escape(str(session_duration)) + '</l>',
        '                    <l>' + escape(proj_name) + '</l>',
        '                </p>',
        '            </sourceDesc>',
        '        </fileDesc>',
        '    </teiHeader>',
        '    <text>',
        '        <body>',
        '            <p source="keystrokes">' + final_xml + '</p>',
        '        </body>',
        '    </text>',
        '</TEI>',
    ]
    return chr(10).join(lines) + chr(10)
