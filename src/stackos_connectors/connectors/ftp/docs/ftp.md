# FTP and explicit FTPS protocol

Source: [reviewed source contract](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/ftp.md).
Condensed during extraction on 2026-10-05; provider documentation was not
reverified live. Installed actions and schemas are defined by the catalog.

Explicit FTPS performs AUTH TLS, logs in, then enables PROT P for protected
data transfers. Plain FTP is a separate transport choice; an FTPS failure
does not imply permission to downgrade. Port, passive mode, timeout and
filename encoding are explicit connection configuration.

Use MLSD/MLST machine-readable facts where available. Browsing can fall back
to NLST plus CWD/SIZE probes; recursive deletion requires reliable entry types.
Do not parse human-formatted LIST output as a portable typed tree.
Relative paths resolve from the current connection's PWD.

Reject NUL, CR and LF in command arguments. Recursive traversal must reject
unsafe child names, unknown types, duplicate names and cycles. A downloaded
child must remain beneath the chosen local destination. A sibling temporary
file plus atomic replacement prevents exposing an incomplete local download.

Transfer conflict choices are overwrite, skip or fail. Preserve completed,
skipped and failed paths if a later item fails; a partial batch is not total
success. Remote symlink traversal has no universal FTP contract.

Recursive deletion has no rollback: preserve each confirmed deletion.
Rename is RNFR followed by RNTO, with server-defined collision behavior.
A rejected rename does not imply download/upload/delete fallback or permission
to pre-delete its destination.

STOR writes to the selected remote path. A lost connection can leave partial
remote bytes. Losing a reply after DELE, MKD, RMD or RNTO can also leave an
unknown mutation outcome. Preserve attempted paths/bytes and known completed
effects; avoid blind retries. Explicit FTP failure replies remain distinguishable
from an unknown post-send outcome.

Sources: [FTP commands](https://www.rfc-editor.org/rfc/rfc959),
[MLST/MLSD](https://www.rfc-editor.org/rfc/rfc3659),
[explicit TLS and PROT P](https://www.rfc-editor.org/rfc/rfc4217),
and [Python ftplib](https://docs.python.org/3/library/ftplib.html).
