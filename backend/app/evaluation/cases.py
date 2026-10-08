"""Questions with a known answer, to measure whether search changes help.

Each case is (question, pattern): the pattern is true only of a chunk that
contains the answer, so a retrieved chunk "answers" the question when the
pattern matches its text. Written against the sample documents in
data/pipeline/raw/test (the Python handbook, the Wake-on-LAN guide, the
pipeline plan) and the Mac / PC comparison. A case whose answer isn't in the
library at all is skipped and reported, so the set stays usable as the
library changes; add the questions that matter for your own documents.
"""

CASES: list[tuple[str, str]] = [
    # Python handbook (172 chunks, many with the same headings: "Syntax:", "Example:", "Summary")
    ("How do I skip the rest of a loop iteration and go to the next one?", r"continue\s+statement skips"),
    ("Which statement is a placeholder that does nothing?", r"pass\s+statement is a placeholder"),
    ("What characters are allowed in a variable name?", r"Variable names can contain letters, numbers, and underscores"),
    ("How do I put a tab or a new line inside a string?", r"Escape sequences are used"),
    ("What does the // operator do?", r"Floor Division"),
    ("How can a function change a variable that lives outside it?", r"To modify a global variable inside a function"),
    ("How do I install an exact version of a library with pip?", r"numpy==1\.20\.0"),
    ("What does *args collect the extra arguments into?", r"collects any extra positional arguments passed to a function into a tuple"),
    ("How do I define my own error type?", r"define your own custom exception classes"),
    ("Which function needs to be imported from functools?", r"must be imported from the functools module"),
    ("Which module handles pattern matching in text?", r"re\s+module provides support for regex"),
    ("When is multithreading a good idea?", r"suitable for I/O-bound tasks"),
    ("Explain abstraction with the car example", r"Think of driving a car"),
    ("What is the special method that runs when an object is created?", r"called the constructor"),
    ("Show an example of a loop that never ends", r"This will run forever"),
    ("What does the strip method do?", r"text\s*\.\s*strip\(\)"),
    # Remote power control guide
    ("Why can't Tailscale turn on a computer that is switched off?", r"cannot wake a completely powered-off"),
    ("What command turns on Wake-on-LAN for the network card?", r"ethtool -s enp6s0 wol g"),
    ("Wake-on-LAN does not work after shutdown, what motherboard setting could cause it?", r"ErP is enabled"),
    ("How do I shut the PC down from the Raspberry Pi?", r"sudo systemctl poweroff"),
    ("Why not use a smart plug to turn the PC off?", r"data loss, filesystem corruption"),
    ("Which kind of SSH key should I generate?", r"ssh-keygen -t ed25519"),
    ("SSH does not work, what do I check?", r"sudo systemctl status ssh"),
    # Pipeline plan
    ("How is access to the API secured?", r"bearer token"),
    ("What is the raw folder in the storage layout?", r"originalcorpus"),
    ("Is the corpus copied onto the server or mounted?", r"mount point to where the data already lives"),
    # Mac / PC comparison
    ("Who gave the custom PC quote and what is its number?", r"Quote No\. 4235"),
    ("Can the two GPUs of the custom PC act as one 64GB pool?", r"two separate 32GB pools"),
    # Message Central guide: a PDF whose text used to be read as nonsense
    ("How do I get a token to authenticate the API calls?", r"authentication token"),
    ("What do I get when I create a free account?", r"free credits"),
]
