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
        self.refs = refs or []
        self.parents = []
        self.children = []
        self.diff = None
    
    def __str__(self):
        """String representation of commit"""
        return f"{self.short_id} - {self.message}"
        
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
    
    @property
    def has_refs(self):
        """Check if commit has references"""
        return len(self.refs) > 0
    
    @property
    def is_merge(self):
        """Check if commit is a merge commit"""
        return len(self.parents) > 1
    
    @property
    def formatted_refs(self):
        """Get formatted references string"""
        if not self.refs:
            return ""
        return f" ({', '.join(self.refs)})"
