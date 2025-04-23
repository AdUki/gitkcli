"""
Git repository interface
"""
import re
from datetime import datetime, timedelta
import pygit2
from pygit2 import GIT_SORT_TOPOLOGICAL, GIT_SORT_TIME, GIT_SORT_REVERSE

from models.commit import GitCommit
from utils.date_parser import parse_date

class Repository:
    """Interface to the Git repository"""
    
    def __init__(self):
        """Initialize repository interface"""
        self.repo = None
        self.commits = []
        self.commit_map = {}
        
    def open(self, path=None):
        """
        Open Git repository at the given path or discover from current directory
        
        Args:
            path (str, optional): Path to repository directory
            
        Returns:
            bool: True if opened successfully, False otherwise
        """
        try:
            self.repo = pygit2.Repository(pygit2.discover_repository(path or '.'))
            return True
        except Exception:
            return False
            
    def load_commits(self, args=None):
        """
        Load commit history using pygit2
        
        Args:
            args (list, optional): Arguments for filtering commits
            
        Returns:
            tuple: List of commits and commit map
        """
        if args is None:
            args = ["--all"]
            
        # Initialize lists and dictionaries
        commits = []
        commit_map = {}
        
        # Get walker references
        walk_refs = self._get_walk_refs(args)
        
        # If no valid references were found, return empty
        if not walk_refs:
            return commits, commit_map
            
        # Create a walker and add starting points
        walker = self._setup_walker(walk_refs)
        
        # Apply filters from arguments
        self._apply_filters(walker, args)
        
        # Process commits
        count = 0
        max_count = self._get_max_count(args)
        
        # Path filters
        path_filters = self._get_path_filters(args)
        
        # Author, date and message filters
        author_filter = self._get_author_filter(args)
        since_time, until_time = self._get_date_filters(args)
        grep_pattern = self._get_grep_pattern(args)
        
        for pygit_commit in walker:
            # Apply filters
            if self._should_skip_commit(pygit_commit, author_filter, since_time, 
                                      until_time, grep_pattern):
                continue
                
            # Create commit object
            commit_id = str(pygit_commit.id)
            author = pygit_commit.author.name
            date = datetime.fromtimestamp(pygit_commit.commit_time).strftime('%Y-%m-%d')
            message = pygit_commit.message.split('\n')[0]  # First line only
            
            commit = GitCommit(commit_id, author, date, message)
            
            # Get parent commits
            for parent in pygit_commit.parents:
                commit.add_parent(str(parent.id))
            
            commits.append(commit)
            commit_map[commit_id] = commit
            
            # Increment counter and check max_count
            count += 1
            if max_count and count >= max_count:
                break
                
        # Connect parents and children
        self._link_commits(commits, commit_map)
        
        # Load refs for commits
        self._load_refs(commit_map)
        
        self.commits = commits
        self.commit_map = commit_map
        return commits, commit_map

    def _get_walk_refs(self, args):
        """Get references to walk based on arguments"""
        walk_refs = []
        
        if "--all" in args:
            # Get all branches (local and remote)
            for ref_name in self.repo.references:
                if ref_name.startswith("refs/heads/") or ref_name.startswith("refs/remotes/"):
                    ref = self.repo.references.get(ref_name)
                    if ref:
                        target_hex = self._get_ref_target_hex(ref)
                        if target_hex not in walk_refs:
                            walk_refs.append(target_hex)
        else:
            # Get HEAD only
            try:
                head = self.repo.head
                if hasattr(head.target, 'hex'):
                    walk_refs.append(head.target.hex)
                else:
                    walk_refs.append(str(head.target))
            except:
                # If HEAD doesn't exist or can't be resolved
                # Try to find a branch to start from
                for ref_name in self.repo.references:
                    if ref_name.startswith("refs/heads/"):
                        ref = self.repo.references.get(ref_name)
                        if ref and hasattr(ref.target, 'hex'):
                            walk_refs.append(ref.target.hex)
                            break
                            
        return walk_refs
    
    def _get_ref_target_hex(self, ref):
        """Get hex string of reference target"""
        if hasattr(ref, 'target'):
            target_oid = ref.target
            # For direct references, target is the OID
            if hasattr(target_oid, 'hex'):
                return target_oid.hex
            # For symbolic references, resolve to the ultimate target
            elif isinstance(target_oid, str):
                try:
                    target_ref = self.repo.references.get(target_oid)
                    if target_ref and hasattr(target_ref.target, 'hex'):
                        return target_ref.target.hex
                    else:
                        return target_oid
                except:
                    return target_oid
            else:
                return str(target_oid)
        return None
    
    def _setup_walker(self, walk_refs):
        """Set up Git revwalk with initial references"""
        walker = self.repo.walk(walk_refs[0], GIT_SORT_TOPOLOGICAL | GIT_SORT_TIME)
        for ref in walk_refs[1:]:
            try:
                # Try to resolve the ref as a string or as an Oid object
                walker.push(ref)
            except ValueError:
                try:
                    # If it's a reference name, get the actual target
                    if self.repo.references.get(ref):
                        oid = self.repo.references.get(ref).target
                        if hasattr(oid, 'hex'):
                            walker.push(oid.hex)
                        else:
                            walker.push(oid)
                except:
                    # Skip invalid refs
                    pass
        return walker
    
    def _apply_filters(self, walker, args):
        """Apply commit filters to the walker"""
        if "--merges" in args:
            walker.hide_non_merges()
        elif "--no-merges" in args:
            walker.hide_merges()
            
        if "--first-parent" in args:
            walker.simplify_first_parent()
            
    def _get_max_count(self, args):
        """Extract max count from arguments"""
        for arg in args:
            if arg.startswith("-n"):
                try:
                    return int(arg[2:])
                except ValueError:
                    pass
        return None
            
    def _get_author_filter(self, args):
        """Extract author filter from arguments"""
        for arg in args:
            if arg.startswith("--author="):
                return arg[9:]
        return None
        
    def _get_date_filters(self, args):
        """Extract date filters from arguments"""
        since_time = None
        until_time = None
        
        for arg in args:
            if arg.startswith("--since="):
                date_str = arg[8:]
                try:
                    since_time = parse_date(date_str)
                except ValueError:
                    pass
            elif arg.startswith("--until="):
                date_str = arg[8:]
                try:
                    until_time = parse_date(date_str)
                except ValueError:
                    pass
                    
        return since_time, until_time
        
    def _get_grep_pattern(self, args):
        """Extract grep pattern from arguments"""
        for arg in args:
            if arg.startswith("--grep="):
                return re.compile(arg[7:], re.IGNORECASE)
        return None
        
    def _get_path_filters(self, args):
        """Extract path filters from arguments"""
        path_filters = []
        if "--" in args:
            idx = args.index("--")
            path_filters = args[idx+1:]
        return path_filters
        
    def _should_skip_commit(self, commit, author_filter, since_time, until_time, grep_pattern):
        """Check if commit should be skipped based on filters"""
        if author_filter and author_filter.lower() not in commit.author.name.lower():
            return True
                
        if since_time and commit.commit_time < since_time:
            return True
                
        if until_time and commit.commit_time > until_time:
            return True
                
        if grep_pattern and not grep_pattern.search(commit.message):
            return True
            
        return False
        
    def _link_commits(self, commits, commit_map):
        """Link commits with parents and children"""
        for commit in commits:
            for parent_id in commit.parents:
                if parent_id in commit_map:
                    commit_map[parent_id].add_child(commit.id)
                    
    def _load_refs(self, commit_map):
        """Load references (branches, tags) for commits"""
        # First get all references
        branches = {}
        tags = {}
        remote_branches = {}
        
        for ref_name in self.repo.references:
            ref = self.repo.references.get(ref_name)
            if not hasattr(ref, 'target'):
                continue
                
            target_hex = ref.target.hex if hasattr(ref.target, 'hex') else ref.target
            
            # Skip if target isn't in our commit map
            if target_hex not in commit_map:
                continue
                
            # Categorize the reference
            if ref_name.startswith('refs/heads/'):
                name = ref_name[11:]  # Local branch
                branches[target_hex] = name
            elif ref_name.startswith('refs/tags/'):
                name = ref_name[10:]  # Tag
                tags[target_hex] = name
            elif ref_name.startswith('refs/remotes/'):
                name = ref_name[13:]  # Remote branch
                remote_branches[target_hex] = name
            else:
                # Other references
                clean_name = ref_name
                if clean_name.startswith('refs/'):
                    clean_name = clean_name.split('/', 2)[-1]
                commit_map[target_hex].add_ref(clean_name)
        
        # Now add the references to the commits
        # Add local branches first, then tags, then remote branches
        for target_hex, name in branches.items():
            commit_map[target_hex].add_ref(name)
            
        for target_hex, name in tags.items():
            # Add tags with a tag indicator
            commit_map[target_hex].add_ref(f"tag: {name}")
            
        for target_hex, name in remote_branches.items():
            # Check if this is tracking a local branch we've already added
            # Skip adding remote branches that match local ones to avoid duplication
            skip = False
            for local_hex, local_name in branches.items():
                if name.endswith('/' + local_name) and target_hex == local_hex:
                    skip = True
                    break
                    
            if not skip:
                commit_map[target_hex].add_ref(name)
        
    def get_commit_diff(self, commit_id):
        """
        Load diff for a specific commit
        
        Args:
            commit_id (str): ID of the commit to diff
            
        Returns:
            list: Formatted diff lines
        """
        if not commit_id or commit_id not in self.commit_map:
            return []
            
        # Get the commit object
        pygit_commit = self.repo.get(commit_id)
        if not pygit_commit:
            return []
            
        # Get parent commit(s)
        if len(pygit_commit.parents) > 0:
            # For regular commits, diff against the first parent
            parent = pygit_commit.parents[0]
            diff = parent.tree.diff_to_tree(pygit_commit.tree)
        else:
            # For initial commit, get the full diff
            diff = pygit_commit.tree.diff_to_tree(swap=True)
            
        # Parse and format the diff
        diff_lines = []
        
        # Add commit header info
        diff_lines.append(('context', f"commit {str(pygit_commit.id)}"))
        diff_lines.append(('context', f"Author: {pygit_commit.author.name} <{pygit_commit.author.email}>"))
        diff_lines.append(('context', f"Date:   {datetime.fromtimestamp(pygit_commit.commit_time).strftime('%a %b %d %H:%M:%S %Y')}"))
        diff_lines.append(('context', ""))
        
        # Add commit message
        for line in pygit_commit.message.split('\n'):
            diff_lines.append(('context', f"    {line}"))
        diff_lines.append(('context', ""))
            
        # Process each patch (file change)
        for patch in diff:
            diff_lines.extend(self._format_patch(patch))
                
        return diff_lines
        
    def _format_patch(self, patch):
        """Format a patch into diff lines"""
        diff_lines = []
        
        # Add file header based on status
        if patch.delta.status_char() == 'A':  # Added file
            diff_lines.append(('file', f"diff --git a/{patch.delta.new_file.path} b/{patch.delta.new_file.path}"))
            diff_lines.append(('file', f"new file mode {patch.delta.new_file.mode:06o}"))
            diff_lines.append(('meta', f"--- /dev/null"))
            diff_lines.append(('meta', f"+++ b/{patch.delta.new_file.path}"))
        elif patch.delta.status_char() == 'D':  # Deleted file
            diff_lines.append(('file', f"diff --git a/{patch.delta.old_file.path} b/{patch.delta.old_file.path}"))
            diff_lines.append(('file', f"deleted file mode {patch.delta.old_file.mode:06o}"))
            diff_lines.append(('meta', f"--- a/{patch.delta.old_file.path}"))
            diff_lines.append(('meta', f"+++ /dev/null"))
        elif patch.delta.status_char() == 'R':  # Renamed file
            diff_lines.append(('file', f"diff --git a/{patch.delta.old_file.path} b/{patch.delta.new_file.path}"))
            diff_lines.append(('file', f"rename from {patch.delta.old_file.path}"))
            diff_lines.append(('file', f"rename to {patch.delta.new_file.path}"))
            diff_lines.append(('meta', f"--- a/{patch.delta.old_file.path}"))
            diff_lines.append(('meta', f"+++ b/{patch.delta.new_file.path}"))
        else:  # Modified file
            diff_lines.append(('file', f"diff --git a/{patch.delta.old_file.path} b/{patch.delta.new_file.path}"))
            diff_lines.append(('meta', f"--- a/{patch.delta.old_file.path}"))
            diff_lines.append(('meta', f"+++ b/{patch.delta.new_file.path}"))
            
        # Add hunks (changed sections)
        for hunk in patch.hunks:
            # Add hunk header
            hunk_header = f"@@ -{hunk.old_start},{hunk.old_lines} +{hunk.new_start},{hunk.new_lines} @@"
            diff_lines.append(('hunk', hunk_header))
            
            # Add lines in the hunk
            for line in hunk.lines:
                content = line.origin + line.content.rstrip('\n')
                if line.origin == '+':
                    diff_lines.append(('add', content))
                elif line.origin == '-':
                    diff_lines.append(('del', content))
                else:  # Context lines
                    diff_lines.append(('context', content))
        
        # Add a separator between files
        diff_lines.append(('context', ""))
        
        return diff_lines
        
    def get_blame(self, commit_id, file_path):
        """
        Get blame information for a file at a specific commit
        
        Args:
            commit_id (str): ID of the commit
            file_path (str): Path to the file
            
        Returns:
            list: Blame data with author, date and content
        """
        try:
            blame_data = []
            
            # Get the commit object
            commit = self.repo.get(commit_id)
            if not commit:
                return blame_data
                
            # Find the file in the commit tree
            try:
                # Get the tree entry for the file
                tree_entry = commit.tree[file_path]
                if not tree_entry or tree_entry.type != pygit2.GIT_OBJECT_BLOB:
                    return blame_data
                    
                # Get the file content
                blob = self.repo.get(tree_entry.id)
                if not blob:
                    return blame_data
                    
                # Run blame on the file
                blame = self.repo.blame(file_path, newest_commit=commit_id)
                
                # Format blame output
                line_num = 1
                for hunk in blame:
                    hunk_commit = self.repo.get(hunk.final_commit_id)
                    author = hunk_commit.author.name
                    short_id = str(hunk_commit.id)[:7]
                    date = datetime.fromtimestamp(hunk_commit.commit_time).strftime('%Y-%m-%d')
                    
                    for _ in range(hunk.lines_in_hunk):
                        try:
                            line_content = blob.data.decode('utf-8').splitlines()[line_num - 1]
                        except (IndexError, UnicodeDecodeError):
                            line_content = "<binary data or decoding error>"
                            
                        blame_data.append((
                            short_id,
                            author,
                            date,
                            line_num,
                            line_content
                        ))
                        line_num += 1
                        
            except (KeyError, ValueError):
                # Handle file not found in tree
                pass
                
            return blame_data
                
        except Exception:
            return []

    def search_commits(self, search_term, search_type="message", use_regex=True):
        """
        Search commits based on different criteria
        
        Args:
            search_term (str): Term to search for
            search_type (str): Type of search ('message', 'path', 'content')
            use_regex (bool): Whether to use regex for searching
            
        Returns:
            list: Indices of commits that match the search
        """
        if not search_term:
            return []
            
        results = []
        
        try:
            # Prepare regex if needed
            pattern = None
            if use_regex:
                try:
                    pattern = re.compile(search_term, re.IGNORECASE)
                except re.error:
                    # If invalid regex, fall back to normal search
                    pattern = None
                    use_regex = False
            
            # Message search is fast, path and content searches need to be optimized
            if search_type == "message":
                # Search in commit messages, authors, and IDs
                for i, commit in enumerate(self.commits):
                    searchable_text = f"{commit.id} {commit.author} {commit.message}".lower()
                    
                    if use_regex and pattern:
                        if pattern.search(searchable_text):
                            results.append(i)
                    elif search_term.lower() in searchable_text:
                        results.append(i)
            
            # For path and content searches, limit to a reasonable number of commits
            # to prevent hanging on large repositories
            elif search_type == "path" or search_type == "content":
                # Limit to the most recent 500 commits for performance
                max_commits = min(500, len(self.commits))
                
                for i in range(max_commits):
                    commit = self.commits[i]
                    
                    # Check if commit matches the search criteria
                    if search_type == "path":
                        if self._commit_touches_path(commit.id, search_term, use_regex, pattern):
                            results.append(i)
                    elif search_type == "content":
                        if self._commit_changes_content(commit.id, search_term, use_regex, pattern):
                            results.append(i)
                
                # If searching a very large repo, add a note about limited results
                if len(self.commits) > max_commits:
                    print(f"Note: Search limited to most recent {max_commits} commits")
                        
        except Exception as e:
            # Log error and return empty results on failure
            print(f"Search error: {e}")
        
        return results
        
    def _commit_touches_path(self, commit_id, path_pattern, use_regex=True, compiled_pattern=None):
        """
        Check if a commit touches a path matching the pattern
        
        Args:
            commit_id (str): Commit ID to check
            path_pattern (str): Path pattern to search for
            use_regex (bool): Whether to use regex matching
            compiled_pattern: Pre-compiled regex pattern
            
        Returns:
            bool: True if commit touches matching path
        """
        try:
            # Get the commit
            commit = self.repo.get(commit_id)
            if not commit:
                return False
                
            # For initial commit with no parents
            if not commit.parents:
                # Get all files in the initial commit
                tree = commit.tree
                for entry in tree:
                    path = entry.name
                    
                    if use_regex and compiled_pattern:
                        if compiled_pattern.search(path):
                            return True
                    elif path_pattern.lower() in path.lower():
                        return True
                return False
                
            # For normal commits, get the diff with parent
            try:
                parent = commit.parents[0]
                # Use a simpler approach to avoid hanging
                diff_options = pygit2.GIT_DIFF_IGNORE_WHITESPACE | pygit2.GIT_DIFF_SKIP_BINARY_CHECK
                diff = parent.tree.diff_to_tree(commit.tree, flags=diff_options)
            
                # Check each file in the diff
                for patch in diff:
                    old_path = patch.delta.old_file.path if hasattr(patch.delta.old_file, 'path') else ''
                    new_path = patch.delta.new_file.path if hasattr(patch.delta.new_file, 'path') else ''
                    
                    paths_to_check = [p for p in [old_path, new_path] if p]
                    
                    for path in paths_to_check:
                        if use_regex and compiled_pattern:
                            if compiled_pattern.search(path):
                                return True
                        elif path_pattern.lower() in path.lower():
                            return True
            except Exception as e:
                print(f"Error in diff: {e}")
                return False
                        
            return False
            
        except Exception as e:
            print(f"Error checking paths: {e}")
            return False
            
    def _commit_changes_content(self, commit_id, content_pattern, use_regex=True, compiled_pattern=None):
        """
        Check if a commit adds or removes content matching the pattern
        
        Args:
            commit_id (str): Commit ID to check
            content_pattern (str): Content pattern to search for
            use_regex (bool): Whether to use regex matching
            compiled_pattern: Pre-compiled regex pattern
            
        Returns:
            bool: True if commit changes matching content
        """
        try:
            # Set a reasonable timeout for content searches to prevent hanging
            MAX_LINES_TO_CHECK = 5000  # Limit number of lines to check
            
            # Get the commit object
            commit = self.repo.get(commit_id)
            if not commit:
                return False
                
            # For initial commits with no parents
            if not commit.parents:
                # Check all files in the initial commit
                tree = commit.tree
                for entry in tree:
                    if entry.type == pygit2.GIT_OBJECT_BLOB:
                        try:
                            blob = self.repo.get(entry.id)
                            content = blob.data.decode('utf-8', errors='replace')
                            
                            # Check content
                            if use_regex and compiled_pattern:
                                if compiled_pattern.search(content):
                                    return True
                            elif content_pattern.lower() in content.lower():
                                return True
                        except Exception:
                            # Skip files that can't be decoded
                            pass
                return False
            
            # For normal commits, check the diff
            try:
                parent = commit.parents[0]
                diff_options = pygit2.GIT_DIFF_IGNORE_WHITESPACE | pygit2.GIT_DIFF_SKIP_BINARY_CHECK
                diff = parent.tree.diff_to_tree(commit.tree, flags=diff_options)
                
                # Process each patch, but limit the number of lines checked
                lines_checked = 0
                for patch in diff:
                    for hunk in patch.hunks:
                        for line in hunk.lines:
                            # Only check added or removed lines
                            if line.origin in ('+', '-'):
                                # Check if we've hit our limit
                                lines_checked += 1
                                if lines_checked > MAX_LINES_TO_CHECK:
                                    return False
                                
                                # Remove the +/- prefix
                                content = line.content.rstrip('\n')
                                
                                # Check content
                                if use_regex and compiled_pattern:
                                    if compiled_pattern.search(content):
                                        return True
                                elif content_pattern.lower() in content.lower():
                                    return True
            except Exception as e:
                print(f"Error in content diff: {e}")
                return False
                
            return False
            
        except Exception as e:
            print(f"Error checking content: {e}")
            return False

    def get_line_origin(self, commit_id, file_path, line_content, line_type):
        """
        Get origin information for a specific line
        
        Args:
            commit_id (str): ID of the commit
            file_path (str): Path to the file
            line_content (str): Content of the line to find origin for
            line_type (str): Type of line ('add', 'del', 'context')
            
        Returns:
            tuple: (origin_commit_id, author, date, commit_message) or None if not found
        """
        try:
            # Extract the line content without prefix (+ or -)
            clean_line_content = line_content
            if line_content.startswith('+') or line_content.startswith('-'):
                clean_line_content = line_content[1:]
                
            # For deleted lines, we need to search in the parent commit
            if line_type == 'del':
                # Get the parent commit
                commit = self.repo.get(commit_id)
                if not commit or not commit.parents:
                    return None
                    
                parent_commit = commit.parents[0]
                parent_commit_id = str(parent_commit.id)
                
                # Try to blame the line in the parent commit where it existed
                blame_data = self.get_blame(parent_commit_id, file_path)
                
                # Find matching line in blame data
                for origin_commit_id, author, date, line_num, content in blame_data:
                    if clean_line_content.strip() == content.strip():
                        # Get the commit message
                        origin_commit = self.repo.get(self.repo.revparse_single(origin_commit_id).id)
                        message = origin_commit.message.split('\n')[0] if origin_commit else "Unknown commit"
                        return (origin_commit_id, author, date, message)
                        
                return None
            else:
                # For added or context lines, search in the current commit
                blame_data = self.get_blame(commit_id, file_path)
                
                # Find matching line in blame data
                for origin_commit_id, author, date, line_num, content in blame_data:
                    if clean_line_content.strip() == content.strip():
                        # Get the commit message
                        origin_commit = self.repo.get(self.repo.revparse_single(origin_commit_id).id)
                        message = origin_commit.message.split('\n')[0] if origin_commit else "Unknown commit"
                        return (origin_commit_id, author, date, message)
                        
                return None
        except Exception as e:
            print(f"Error in get_line_origin: {e}")
            return None
