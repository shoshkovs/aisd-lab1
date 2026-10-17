# Shunting yard fuzzing report

- Generated: 2026-10-17 10:02:45
- Platform: `darwin` (executable suffix `(none)`)
- **MD5 of all C++ files: `21f8ccedcf2ed94e4f4b415cde26f428`** (5 files)
- Executable: `main`
- Executable MD5: `b1759a2f2d80b9fb03224ad718bfe914` (84600 bytes)
- Compiled with: `/usr/bin/g++ -std=c++11 -g /Users/alexsho/Documents/05_PROJECTS/LETI/АиСд/Л1/main.cpp /Users/alexsho/Documents/05_PROJECTS/LETI/АиСд/Л1/list.cpp -o /Users/alexsho/Documents/05_PROJECTS/LETI/АиСд/Л1/main`
- Reference: Python oracle implementing the same algorithm (tokens `+ - * / ^ sin cos ( ) 0-9`, space separated)
- Random seed: `20261012` (re-run with `--seed 20261012` to reproduce)
- Fuzz runs requested: 1000 (max nesting depth 3, parentheses: mixed)
- Every generated expression is well-formed: the generator parses its own output back with the reference grammar and refuses to continue when the parentheses do not match the tree
- Per-run timeout: 5s
- Duration: 3.08s

## Verdict

**PASS**

| Metric | Value |
| --- | --- |
| Total executions | 1013 |
| 10 default bound examples | 10 |
| Rejection (error handling) tests | 3 |
| Random fuzz runs | 1000 |
| - minimal parentheses | 462 |
| - fully parenthesised | 538 |
| Errors found | **0** |
| Fuzz-stage errors | 0 |
| Pass rate | 100.00% |

## C++ files hashed (MD5)

Aggregate MD5 of all C++ files: **`21f8ccedcf2ed94e4f4b415cde26f428`**

| File | Size (bytes) | MD5 |
| --- | --- | --- |
| `DoublyLinkedList.h` | 437 | `56f55a5881a1fb25e79f2713a9907cff` |
| `DynamicArray.h` | 1512 | `0c4dc59101414f14be2f7cab238fbd50` |
| `Stack.h` | 395 | `22aa7012c148da05bbf62d321ee4768e` |
| `list.cpp` | 1403 | `8c6553b7bc4a232f0ae464f03fca09e4` |
| `main.cpp` | 6265 | `f8b7134c22f57c563027b06acd6b220d` |

| Artifact | Size (bytes) | MD5 |
| --- | --- | --- |
| `main` (executable) | 84600 | `b1759a2f2d80b9fb03224ad718bfe914` |

## Default bound test examples

| # | Input | Expected RPN | Target | Result |
| --- | --- | --- | --- | --- |
| 1 | `5` | `5` | `5` | PASS |
| 2 | `0 + 0` | `0 0 +` | `0 0 +` | PASS |
| 3 | `123 + 456 * 789` | `123 456 789 * +` | `123 456 789 * +` | PASS |
| 4 | `1 + 3 - 4` | `1 3 + 4 -` | `1 3 + 4 -` | PASS |
| 5 | `2 ^ 3 ^ 2` | `2 3 2 ^ ^` | `2 3 2 ^ ^` | PASS |
| 6 | `1 + 2 * 3 ^ 4 - 5 / 6` | `1 2 3 4 ^ * + 5 6 / -` | `1 2 3 4 ^ * + 5 6 / -` | PASS |
| 7 | `( 1 + 2 ) * 3` | `1 2 + 3 *` | `1 2 + 3 *` | PASS |
| 8 | `( ( 1 + 2 ) * ( 3 - 4 ) ) ^ 2` | `1 2 + 3 4 - * 2 ^` | `1 2 + 3 4 - * 2 ^` | PASS |
| 9 | `sin ( 0 ) + cos ( 9 )` | `0 sin 9 cos +` | `0 sin 9 cos +` | PASS |
| 10 | `2 + sin ( 5 + 3 ) * 8 + ( 9 * 7 )` | `2 5 3 + sin 8 * + 9 7 * +` | `2 5 3 + sin 8 * + 9 7 * +` | PASS |

## Error handling tests

| Input | Expected | Result |
| --- | --- | --- |
| `( 1 + 2` | reject: mismatched parentheses | PASS (exit non-zero) |
| `1 + 2 )` | reject: mismatched parentheses | PASS (exit non-zero) |
| `1 # 2` | reject: unknown token: # | PASS (exit non-zero) |

## Error breakdown

No errors: every execution matched the reference implementation.

## Notes

- The MD5 hashes cover every C++ file of the project: the listed sources, all `.cpp`/`.hpp`/`.h` files next to them, and any `--hash-file`.
  The aggregate hash is computed from the sorted `<path>:<md5>` lines, so it changes whenever any file changes, is added, removed or renamed,
  and it does not depend on the order the files were listed in or on the absolute path of the project.
- The executable MD5 identifies the exact binary that was tested, but it may differ between builds of identical sources because some
  linkers (e.g. Mach-O on macOS) embed a random UUID. The aggregate C++ MD5 is the stable identity of the sources.
- The oracle mirrors `main.cpp`: precedence `+ -` < `* /` < `^` < `sin cos`, `^` right-associative, everything else left-associative,
  parentheses group subexpressions, a function directly before `(` is emitted after the group.
- Every fuzz input is a *correct* arithmetic expression over the required operations `+ - * / ^ sin cos ( )` and `0-9`:
  the generator builds an expression tree, renders it, parses the text back with the reference grammar and re-renders it in minimal
  form. Text and tree have to agree, otherwise the generator aborts instead of feeding a wrong corpus to the target.
- Parentheses are emitted only where precedence and associativity require them (`--parens min`), around every subexpression
  (`--parens full`), or either one per expression (`--parens mixed`, the default). Both styles are checked against the same oracle.
- Malformed input is *not* generated: the only rejection cases are the fixed `## Error handling tests` above, which still verify that the
  target reports unknown tokens and mismatched parentheses on stderr and exits non-zero.
- A passing run means the compiled target agrees with the reference on every bound and fuzz case, with no crash, timeout or stray stderr output.
