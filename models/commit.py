"""
Git commit model class
"""

class GitCommit:
    """Represents a Git commit with its metadata and relationships"""
    
    def __init__(self, commit_id, author, date, message, refs=None):
        """
        Initialize a new GitCommit object.
        
        Args:
            commit_id (str): Full commit hash
            author (str): Author name
            date (str): Formatted date string
            message (str): Commit message
            refs (list, optional): Reference names (branches/tags)
        """
        self.id = commit_id
        self.short_id = commit_id[:7]
        self.author = author
        self.date = date
        self.message = message
        self.title = self.message.split('\n')[0]  # First line only
        self.refs = refs or []
        self.parents = []
        self.children = []
        self.diff = None
        # Track branch info for tree view
        self.branch_pos = -1  # Position in branch tree
        self.branch_lines = []  # Drawing lines for this commit
        self.paths = None
        self.content = None
    
    def __str__(self):
        """String representation of commit"""
        return f"{self.short_id} - {self.title}"
        
    def add_parent(self, parent_id):
        """Add a parent commit ID"""
        if parent_id not in self.parents:
            self.parents.append(parent_id)
            
    def add_child(self, child_id):
        """Add a child commit ID"""
        if child_id not in self.children:
            self.children.append(child_id)
    
    def add_ref(self, ref_name):
        """Add a reference name (branch/tag)"""
        if ref_name not in self.refs:
            self.refs.append(ref_name)
            
    def get_changed_paths(self, repository):
        """
        Get all file paths that were changed in this commit.
        This method retrieves paths on demand using the repository.
        
        Args:
            repository: Repository instance with access to pygit2
            
        Returns:
            list: List of file paths changed in this commit
        """
        if self.paths is not None:
            return self.paths

        paths = []
        try:
            # Get the pygit2 commit object
            pygit_commit = repository.repo.get(self.id)
            if not pygit_commit:
                return paths
                
            # For initial commit with no parents
            if len(pygit_commit.parents) == 0:
                # Get all files in the initial commit tree
                self._collect_paths_from_tree(repository.repo, pygit_commit.tree, paths)
            else:
                # For normal commits, get changed paths from diff with parent
                parent = pygit_commit.parents[0]
                diff = parent.tree.diff_to_tree(pygit_commit.tree)
                
                # Collect paths from the diff
                for patch in diff:
                    # Add old file path (for deletions/modifications)
                    if hasattr(patch.delta.old_file, 'path'):
                        old_path = patch.delta.old_file.path
                        if old_path and old_path not in paths:
                            paths.append(old_path)
                            
                    # Add new file path (for additions/modifications)
                    if hasattr(patch.delta.new_file, 'path'):
                        new_path = patch.delta.new_file.path
                        if new_path and new_path not in paths:
                            paths.append(new_path)
                            
        except Exception as e:
            # In case of errors, return whatever paths we collected
            print(f"Error getting paths for commit {self.id}: {e}")
            
        self.paths = paths.copy()
        return self.paths
            
    def _collect_paths_from_tree(self, repo, tree, paths, prefix=""):
        """
        Recursively collect all paths from a tree
        
        Args:
            repo: pygit2 repository object
            tree: Tree object
            paths: List to collect paths into
            prefix: Prefix for nested paths
        """
        if not tree:
            return
            
        try:
            for entry in tree:
                # Add this entry's path
                path = f"{prefix}{entry.name}"
                if path not in paths:
                    paths.append(path)
                
                # If this is a directory, recursively collect paths
                if entry.type == 3:  # 3 = GIT_OBJ_TREE
                    subtree = repo.get(entry.id)
                    if subtree:
                        self._collect_paths_from_tree(repo, subtree, paths, f"{path}/")
        except Exception as e:
            print(f"Error collecting paths from tree: {e}")
            
    def matches_path_pattern(self, repository, path_pattern):
        """
        Check if this commit touches any paths matching the given pattern
        
        Args:
            repository: Repository instance
            path_pattern: String pattern to match against paths
            
        Returns:
            bool: True if any path matches, False otherwise
        """
        # Get paths changed by this commit
        paths = self.get_changed_paths(repository)
        
        # Check if any path matches the pattern (case-insensitive)
        path_pattern = path_pattern.lower()
        for path in paths:
            if path_pattern in path.lower():
                return True
                
        return False

    def matches_content_pattern(self, repository, content_pattern):
        """
        Check if this commit contains content changes matching the given pattern
        
        Args:
            repository: Repository instance
            content_pattern: String pattern to match against changed content
            
        Returns:
            bool: True if any content changes match, False otherwise
        """
        if self.content is not None: 
            return content_pattern.lower() in self.content.lower()
        try:
            # Set a reasonable safety limit for search to avoid performance issues
            MAX_LINES_TO_CHECK = 5000
            lines_checked = 0
            
            # Get the pygit2 commit object
            pygit_commit = repository.repo.get(self.id)
            if not pygit_commit:
                return False
            
            # For initial commit with no parents
            if len(pygit_commit.parents) == 0:
                # Check all files in the initial commit tree
                tree = pygit_commit.tree
                for entry in tree:
                    # Only check blob entries (files)
                    if entry.type != 3:  # 3 is GIT_OBJ_TREE
                        try:
                            # Get blob content
                            blob = repository.repo.get(entry.id)
                            if blob and hasattr(blob, 'data'):
                                # Try to decode content as text
                                try:
                                    content = blob.data.decode('utf-8', errors='replace')
                                    
                                    # Check if content contains pattern (case-insensitive)
                                    self.content = content
                                    if content_pattern.lower() in content.lower():
                                        return True
                                        
                                    # Count lines for safety limit
                                    lines_checked += content.count('\n') + 1
                                    if lines_checked > MAX_LINES_TO_CHECK:
                                        return False
                                except UnicodeDecodeError:
                                    # Skip binary files
                                    pass
                        except Exception:
                            # Skip problematic files
                            pass
            else:
                # For normal commits, check the diff with parent
                try:
                    parent = pygit_commit.parents[0]
                    # Use options to optimize performance
                    diff_options = 0  # Use default options
                    diff = parent.tree.diff_to_tree(pygit_commit.tree, flags=diff_options)
                    
                    # Process each patch in the diff
                    for patch in diff:
                        # Process each hunk (changed section) in the patch
                        for hunk in patch.hunks:
                            # Process each line in the hunk
                            for line in hunk.lines:
                                # Only check added or removed lines
                                if line.origin in ('+', '-'):
                                    # Check if we've hit our limit
                                    lines_checked += 1
                                    if lines_checked > MAX_LINES_TO_CHECK:
                                        return False
                                    
                                    # Remove the +/- prefix
                                    content = line.content.rstrip('\n')
                                    
                                    # Check content against pattern (case-insensitive)
                                    self.content = content
                                    if content_pattern.lower() in content.lower():
                                        return True
                except Exception as e:
                    # On error, return False
                    print(f"Error in content diff for {self.id}: {e}")
                    return False
                    
            return False
            
        except Exception as e:
            print(f"Error checking content for commit {self.id}: {e}")
            return False
    
    @property
    def has_refs(self):
        """Check if commit has references"""
        return len(self.refs) > 0
    
    @property
    def is_merge(self):
        """Check if commit is a merge commit"""
        return len(self.parents) > 1
    
    @property
    def branch_count(self):
        """Get number of branches involved with this commit"""
        return len(self.parents) + len(self.children)
    
    @property
    def formatted_refs(self):
        """Get formatted references string"""
        if not self.refs:
            return ""
        
        head_refs = []
        branch_refs = []
        remote_refs = []
        tag_refs = []
        
        # Sort refs by type
        for ref in self.refs:
            if ref.startswith("HEAD"):
                head_refs.append(ref)
            elif ref.startswith("tag:"):
                tag_refs.append(ref)
            elif "/" in ref:  # Remote branches typically have a slash
                remote_refs.append(ref)
            else:
                branch_refs.append(ref)
        
        # Combine refs in order: HEAD, branches, remotes, tags
        all_sorted_refs = head_refs + branch_refs + remote_refs + tag_refs
        return f" ({', '.join(all_sorted_refs)})"
