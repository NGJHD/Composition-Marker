"""Singapore education levels, and what a composition at each one looks like.

This file is the calibration. The model knows the phrase "Primary 4" and it
knows what a ten-year-old is, but neither on its own holds a standard steady:
asked to mark a P4 script cold, it drifts towards an adult one and scores every
child in the fifties. So the prompt carries three things, not one -- the level,
the age, and an explicit written expectation for that level -- and the third is
what actually does the work.

Nothing here claims to reproduce an official MOE marking scheme. The score is a
band judgement in the MOE style (Content, Language, Organisation), and the
length figures are the ordinary expectations of the level rather than a rule
any syllabus states. What matters is that they are fixed and written down, so
two runs of the same script are marked against the same thing.
"""

from __future__ import annotations

# Displayed newest-to-youngest, which is how the dropdown reads.
#
# The ages are the standard Singapore cohort ages: P1 at 7 through P6 at 12,
# Secondary 1 at 13 through Secondary 4 at 16, Secondary 5 (the N(A) year) at
# 17, JC1 at 17 and JC2 at 18. A child sitting a level a year early or late is
# marked against the level, not the birthday -- which is why the level is what
# the dropdown asks for.
LEVELS = [
    {
        "key": "jc2", "label": "JC2", "age": 18, "stage": "jc",
        "en": {
            "length": "500-800 words",
            "forms": "argumentative or discursive essay on a general topic",
            "expected": (
                "A sustained argument with a clear position held across the "
                "whole essay. Paragraphs built on topic sentences and "
                "developed with specific, real examples rather than "
                "generalities. At least one counter-argument taken seriously "
                "and answered. Precise, varied vocabulary; controlled complex "
                "sentences; consistent register."
            ),
            "not_yet": (
                "Do not require original scholarship or statistics quoted to "
                "the decimal. A well-chosen, accurately described example is "
                "what is expected at this level."
            ),
        },
        "zh": {
            "length": "700-900字",
            "forms": "议论文或论说文",
            "expected": (
                "论点明确并贯穿全文，论据具体、真实、有说服力，能够提出并回应"
                "反面意见。段落之间有清楚的过渡，语言准确精炼，能恰当运用成语与"
                "书面语。"
            ),
            "not_yet": "不必要求引用统计数据，能准确说明一个恰当的例子即可。",
        },
    },
    {
        "key": "jc1", "label": "JC1", "age": 17, "stage": "jc",
        "en": {
            "length": "500-700 words",
            "forms": "argumentative or discursive essay on a general topic",
            "expected": (
                "A clear position, developed in paragraphs that each make one "
                "point and support it. Examples that are specific rather than "
                "hypothetical. Some acknowledgement of the other side. Varied "
                "sentence structure and a serious, consistent register."
            ),
            "not_yet": (
                "The argument may still be one-sided in places, and a "
                "conclusion that summarises rather than synthesises is not yet "
                "a serious fault."
            ),
        },
        "zh": {
            "length": "600-800字",
            "forms": "议论文",
            "expected": (
                "立场清楚，每段围绕一个论点展开并有具体例子支持，语言通顺，"
                "用词较为准确，段落之间有过渡。"
            ),
            "not_yet": "论证可以稍显单薄，结尾若只是总结全文，尚不算严重缺点。",
        },
    },
    {
        "key": "s5", "label": "Secondary 5", "age": 17, "stage": "secondary",
        "en": {
            "length": "350-500 words",
            "forms": "narrative, personal recount, descriptive or argumentative",
            "expected": (
                "A complete piece with a clear shape and a definite ending. "
                "Paragraphs that each do one job. Accurate common tenses, "
                "correct dialogue punctuation, and vocabulary chosen for the "
                "situation rather than reached for. Some variety in sentence "
                "openers."
            ),
            "not_yet": (
                "Occasional slips in agreement or prepositions are normal at "
                "this level and should be noted once, not counted repeatedly."
            ),
        },
        "zh": {
            "length": "450-550字",
            "forms": "记叙文、说明文或议论文",
            "expected": (
                "结构完整，重点突出，语句通顺，能恰当使用连接词与描写，"
                "标点使用正确。"
            ),
            "not_yet": "偶尔出现错别字或病句属于此级别的正常现象，指出一次即可。",
        },
    },
    {
        "key": "s4", "label": "Secondary 4", "age": 16, "stage": "secondary",
        "en": {
            "length": "350-500 words",
            "forms": "narrative, personal recount, descriptive or argumentative",
            "expected": (
                "A piece that fulfils the task from the first line and ends "
                "deliberately. Controlled paragraphing, accurate tense and "
                "agreement, correctly punctuated dialogue, and description "
                "that shows rather than tells. Sentence structure varied on "
                "purpose, not by accident."
            ),
            "not_yet": (
                "Ambitious vocabulary used slightly wrongly should be "
                "corrected but credited as an attempt, not treated as a worse "
                "error than a safe word used correctly."
            ),
        },
        "zh": {
            "length": "450-550字",
            "forms": "记叙文、说明文或议论文",
            "expected": (
                "内容切题，详略得当，能通过细节描写表达感受；段落分明，"
                "语句通顺，成语与修辞使用恰当。"
            ),
            "not_yet": "用词稍有生硬但方向正确的尝试，应给予肯定并顺带纠正。",
        },
    },
    {
        "key": "s3", "label": "Secondary 3", "age": 15, "stage": "secondary",
        "en": {
            "length": "300-400 words",
            "forms": "narrative, personal recount or descriptive; simple argumentative",
            "expected": (
                "A clear beginning, middle and end, with the main incident "
                "given the most space. Paragraphs used properly. Consistent "
                "past tense in a narrative. Some descriptive detail and a "
                "attempt at varied sentence openers."
            ),
            "not_yet": (
                "Argument writing is still new here: a reasonable point with "
                "one supporting example is enough, and a missing "
                "counter-argument is not a serious fault."
            ),
        },
        "zh": {
            "length": "400-500字",
            "forms": "记叙文或简单的议论文",
            "expected": (
                "有明确的中心思想，情节完整，能使用一定的描写；分段合理，"
                "语句大致通顺。"
            ),
            "not_yet": "议论文刚起步，能提出一个观点并举一个例子说明即可。",
        },
    },
    {
        "key": "s2", "label": "Secondary 2", "age": 14, "stage": "secondary",
        "en": {
            "length": "300-350 words",
            "forms": "narrative, personal recount or descriptive",
            "expected": (
                "A story with a real problem and a resolution, not just a "
                "sequence of events. Paragraphs at each change of time, place "
                "or speaker. Consistent tense. Dialogue punctuated correctly. "
                "Some feeling conveyed through action rather than stated."
            ),
            "not_yet": (
                "Vocabulary is still growing: plain words used accurately are "
                "worth more than idioms dropped in without fit."
            ),
        },
        "zh": {
            "length": "350-450字",
            "forms": "记叙文",
            "expected": (
                "故事有起因、经过和结果，能围绕一件事写清楚；会分段，"
                "标点正确，能使用简单的描写。"
            ),
            "not_yet": "成语若使用不当，应指出正确用法，但不必因此大幅扣分。",
        },
    },
    {
        "key": "s1", "label": "Secondary 1", "age": 13, "stage": "secondary",
        "en": {
            "length": "250-300 words",
            "forms": "narrative or personal recount",
            "expected": (
                "One clear incident told in order, with a beginning that sets "
                "the scene and an ending that closes it. Paragraphs used. "
                "Mostly consistent past tense. Simple dialogue, punctuated."
            ),
            "not_yet": (
                "Sentences may still be mainly simple and compound. Complex "
                "sentences are a bonus here, not an expectation."
            ),
        },
        "zh": {
            "length": "300-400字",
            "forms": "记叙文",
            "expected": (
                "能按顺序写清楚一件事，有开头和结尾，会分段，"
                "标点符号使用基本正确。"
            ),
            "not_yet": "句子以简单句为主是正常的，能写出长句是加分而非要求。",
        },
    },
    {
        "key": "p6", "label": "Primary 6", "age": 12, "stage": "primary",
        "en": {
            "length": "150-250 words",
            "forms": "narrative from a given topic and pictures",
            "expected": (
                "A story that answers the topic directly, with a beginning "
                "that pulls the reader in and an ending that resolves it. "
                "Clear paragraphs. Consistent past tense. Apt vocabulary and "
                "the occasional well-used idiom. Feelings shown through what "
                "the character does, not only stated."
            ),
            "not_yet": (
                "This is the PSLE standard. Mark it as such: a competent, "
                "complete, correctly paragraphed story is a good script, and "
                "does not need literary flourish to score well."
            ),
        },
        "zh": {
            "length": "至少150字，一般200-300字",
            "forms": "命题作文或看图作文",
            "expected": (
                "内容切题，有开头、经过和结尾，能写出人物的心情和动作；"
                "会分段，标点正确，能恰当使用一两个成语。"
            ),
            "not_yet": "这是小六会考的水平，内容完整、语句通顺已属良好，不必要求华丽辞藻。",
        },
    },
    {
        "key": "p5", "label": "Primary 5", "age": 11, "stage": "primary",
        "en": {
            "length": "150-200 words",
            "forms": "narrative from a given topic and pictures",
            "expected": (
                "A clear plot with a problem and how it was solved. "
                "Paragraphs. Past tense held throughout. Sentence openers that "
                "are not all 'I' or 'Then'. Some description of how things "
                "looked or felt."
            ),
            "not_yet": (
                "Dialogue may be sparse and punctuation of it imperfect. "
                "Correct it, but do not let it dominate the language mark."
            ),
        },
        "zh": {
            "length": "150-250字",
            "forms": "命题作文或看图作文",
            "expected": (
                "能围绕题目写一件完整的事，有简单的心理描写；分段清楚，"
                "标点正确。"
            ),
            "not_yet": "对话描写可以较少，若标点有误，指出即可。",
        },
    },
    {
        "key": "p4", "label": "Primary 4", "age": 10, "stage": "primary",
        "en": {
            "length": "120-150 words",
            "forms": "short narrative, often from pictures",
            "expected": (
                "A beginning, middle and end in the right order. Paragraphs "
                "beginning to be used. Past tense mostly consistent. Simple "
                "dialogue with speech marks. A few describing words used "
                "sensibly."
            ),
            "not_yet": (
                "Do not expect figurative language, varied clause structure, "
                "or a twist. A clear, correctly ordered, correctly punctuated "
                "story is exactly what a strong Primary 4 script looks like."
            ),
        },
        "zh": {
            "length": "120-180字",
            "forms": "短篇记叙文或看图作文",
            "expected": (
                "能按顺序写清楚一件事，有开头和结尾；会使用逗号和句号，"
                "开始尝试分段。"
            ),
            "not_yet": "不必要求成语或修辞，句子通顺、意思清楚就是好文章。",
        },
    },
    {
        "key": "p3", "label": "Primary 3", "age": 9, "stage": "primary",
        "en": {
            "length": "100-120 words",
            "forms": "short narrative, usually from pictures",
            "expected": (
                "Events told in the right order with a beginning and an end. "
                "Capital letters and full stops used. Mostly consistent past "
                "tense. Simple joining words -- and, but, because, then."
            ),
            "not_yet": (
                "Paragraphing is only just being taught. Note its absence "
                "gently and do not treat it as a major organisation failure."
            ),
        },
        "zh": {
            "length": "100-150字",
            "forms": "看图作文",
            "expected": (
                "能按图意把事情写清楚，有开头和结尾，会使用逗号和句号，"
                "能用上「先……然后……最后……」一类的顺序词。"
            ),
            "not_yet": "分段刚开始学习，若未分段，温和提醒即可。",
        },
    },
    {
        "key": "p2", "label": "Primary 2", "age": 8, "stage": "primary",
        "en": {
            "length": "60-80 words",
            "forms": "a short paragraph, usually from pictures",
            "expected": (
                "Several sentences that follow on from each other and tell one "
                "small story. Capital letters at the start and full stops at "
                "the end. Sequencing words such as first, then, finally."
            ),
            "not_yet": (
                "Repeated sentence openers and very simple vocabulary are "
                "entirely normal here. Praise what is correct before "
                "correcting anything."
            ),
        },
        "zh": {
            "length": "60-100字",
            "forms": "看图写话",
            "expected": (
                "能写出几句连贯的话，把图意说清楚，会用句号，"
                "能使用「先」「然后」「最后」。"
            ),
            "not_yet": "用词简单、句式重复是正常的，应先肯定写对的地方。",
        },
    },
    {
        "key": "p1", "label": "Primary 1", "age": 7, "stage": "primary",
        "en": {
            "length": "40-60 words",
            "forms": "three to five sentences, from pictures",
            "expected": (
                "Complete sentences that make sense and relate to the picture. "
                "A capital letter at the start and a full stop at the end. "
                "Words spelled as taught, with sensible attempts at the rest."
            ),
            "not_yet": (
                "This child has been writing for months, not years. Invented "
                "spellings that sound right, missing joining words and a "
                "missing ending are all normal. Be warm; correct two or three "
                "things at most."
            ),
        },
        "zh": {
            "length": "40-80字",
            "forms": "看图写话",
            "expected": (
                "能写出几句完整、通顺的话，会用句号，字迹清楚。"
            ),
            "not_yet": (
                "刚学写作不久，出现错别字、句子简短都是正常的，"
                "应以鼓励为主，最多指出两三处。"
            ),
        },
    },
]

BY_KEY = {lv["key"]: lv for lv in LEVELS}

# The level's name as a Chinese-medium teacher would write it. "新加坡 Primary 4"
# in an otherwise Chinese rubric reads as a translation left half-finished, and
# the rubric is the one part of the prompt that has to sound authoritative.
ZH_LABELS = {
    "p1": "小学一年级", "p2": "小学二年级", "p3": "小学三年级",
    "p4": "小学四年级", "p5": "小学五年级", "p6": "小学六年级",
    "s1": "中一", "s2": "中二", "s3": "中三", "s4": "中四", "s5": "中五",
    "jc1": "初级学院一年级", "jc2": "初级学院二年级",
}

DEFAULT_KEY = "p5"

LANGUAGES = [
    {"key": "en", "label": "English"},
    {"key": "zh", "label": "Chinese"},
]

# What each score band means, in the MOE style: Content, Language and
# Organisation judged separately and totalled to 100. The band descriptors are
# deliberately relative to the level -- "for this level" appears in every one of
# them, because that is the whole point of the exercise.
BANDS_EN = """\
Band 5  (85-100)  Outstanding for this level. Nothing of substance to fix.
Band 4  (70-84)   Strong for this level. Fully meets it, with real strengths.
Band 3  (55-69)   Competent for this level. Meets it, with clear room to grow.
Band 2  (40-54)   Developing. Parts work; the piece falls short in places.
Band 1  (0-39)    Well below the level, or the task was not attempted.
"""


def marks_table(language: str = "en") -> str:
    """The criteria and their weights, as the prompt states them."""
    # Organisation deliberately does NOT mention paragraphing. The transcript
    # cannot preserve it -- this child indents rather than leaving a blank line
    # and the indent is lost in the reading -- so a criterion that named it
    # would have the model deducting for work it simply cannot see.
    if language == "zh":
        return (
            "内容 Content 40分：切题、内容充实、有中心思想、详略得当。\n"
            "语文 Language 40分：用词准确、语句通顺、标点与错别字、"
            "描写与修辞是否恰当。\n"
            "组织 Organisation 20分：开头与结尾、条理是否清楚、"
            "情节先后顺序、前后是否连贯。"
        )
    return (
        "Content 40 marks: relevance to the topic, interest and development of "
        "ideas, and whether the piece does what it set out to do.\n"
        "Language 40 marks: grammar, tense, spelling, punctuation, vocabulary "
        "and sentence variety.\n"
        "Organisation 20 marks: the opening and the ending, the order events "
        "are told in, and whether each idea leads to the next."
    )


def expectations_block(level_key: str, language: str = "en") -> str:
    """The written standard for one level, as it goes into the prompt."""
    level = BY_KEY.get(level_key) or BY_KEY[DEFAULT_KEY]
    spec = level.get(language) or level["en"]
    if language == "zh":
        return (
            "年级：新加坡%s（%s，学生年龄约 %d 岁）\n"
            "文体：%s\n"
            "篇幅：%s\n"
            "本年级应达到的水平：%s\n"
            "本年级尚不要求：%s"
            % (ZH_LABELS.get(level["key"], level["label"]), level["label"],
               level["age"], spec["forms"], spec["length"],
               spec["expected"], spec["not_yet"])
        )
    return (
        "Level: Singapore %s (the child is about %d years old)\n"
        "Usual form at this level: %s\n"
        "Expected length: %s\n"
        "What a strong script at this level does: %s\n"
        "What is NOT yet expected at this level: %s"
        % (level["label"], level["age"], spec["forms"], spec["length"],
           spec["expected"], spec["not_yet"])
    )


def label(level_key: str) -> str:
    """The level's name, or "" if the key means nothing.

    Deliberately not defaulting to Primary 5 here, unlike expectations_block:
    a marking run always has a real level, but a composition reopened from a
    folder with no sidecar has none, and captioning it "Primary 5" states
    something untrue about a child's work. Nothing is the honest answer, and
    the history list simply leaves the label out.
    """
    level = BY_KEY.get(level_key)
    return level["label"] if level else ""


def age(level_key: str) -> int:
    level = BY_KEY.get(level_key)
    return level["age"] if level else 0


# Where the improved rewrite aims: one level above the child's own.
#
# The point is to show the way ahead -- a model answer at the child's own level
# shows them what they nearly managed, while one level up shows them what they
# are working towards and is still close enough to learn from.
#
# Secondary 5 is skipped on the way up: it is the N-level year, a different
# route rather than a step beyond Secondary 4, so Secondary 4 aims at JC1 and
# Secondary 5 does too. JC2 has nothing above it in school, so it aims at a
# capable adult writing for a general reader.
NEXT_LEVEL = {
    "p1": "p2", "p2": "p3", "p3": "p4", "p4": "p5", "p5": "p6",
    "p6": "s1", "s1": "s2", "s2": "s3", "s3": "s4",
    "s4": "jc1", "s5": "jc1", "jc1": "jc2", "jc2": "adult",
}

ADULT_LABEL = "an educated adult writing for a general reader"

ADULT_EXPECTATION = {
    "en": (
        "Target standard: %s -- beyond school level.\n"
        "What that means here: an argument or a narrative that holds together "
        "from first line to last, specific and concrete rather than general, "
        "with sentence rhythm varied on purpose, no padding, and a close that "
        "lands. Precise vocabulary used accurately; nothing reached for."
    ) % ADULT_LABEL,
    "zh": (
        "目标水平：成人写作水平，已超出中学与初级学院的范围。\n"
        "具体而言：结构完整、前后呼应，论述或叙述具体而不空泛，"
        "句式有意变化，没有废话，结尾有力。用词准确、自然，不堆砌辞藻。"
    ),
}


def target_block(level_key: str, language: str = "en") -> str:
    """The standard the improved rewrite should be written to.

    One level above the composition's own. Returns the same shape of block as
    expectations_block so the rewrite prompt reads consistently, with a line
    saying plainly which level it is aiming at and why.
    """
    target = NEXT_LEVEL.get(level_key, level_key)
    if target == "adult":
        return ADULT_EXPECTATION.get(language, ADULT_EXPECTATION["en"])
    block = expectations_block(target, language)
    if language == "zh":
        return ("这是改写的目标水平，比学生目前的年级高一级：\n\n%s" % block)
    return ("This is the standard to write the improved version to. It is one "
            "level ABOVE the child's own, deliberately:\n\n%s" % block)


def target_label(level_key: str) -> str:
    target = NEXT_LEVEL.get(level_key, level_key)
    return ADULT_LABEL if target == "adult" else (label(target) or "")


def choices() -> list:
    """What the dropdown needs: key, label and the age it corresponds to."""
    return [{"key": lv["key"], "label": lv["label"], "age": lv["age"],
             "stage": lv["stage"]} for lv in LEVELS]
