## `ecoli.shared`: a sub-package containing "utils-like" logic, common data models, commonly used scripts/workflows, and encrypted python modules.

### Encrypted Python Modules:
This sub-package contains some sensitive encryption/CUI logic that cannot be publicly shared. In the interest of trusted collaboration
and reproducibility, these files have been encrypted using `gpg` unlocked by a secret unique passphrase (for each encrypted module) rather
than completely omitted/gitignore-d.

#### NOTES:
- _*The passphrase can only be accessed by contacting the development team*_. 
- Collaborators and developers in active dev should be careful to ensure the following: A.) that the original `<MODULENAME>.py` file is added to this repo's
    `.gitignore` and B.) that the most recent version of module as been encrypted using the `ecoli/shared/scripts/write_gpg.sh` script (See script for more details.)
- Trusted collaborators and developers can decrypt the given encrypted `<MODULENAME>.py.gpg` file with the `read_gpg.sh` script found in the 
    same aforementioned directory.