"""sublime-rsync-ssh: A Sublime Text 3 plugin for syncing local folders to remote servers."""
import sublime, sublime_plugin
import subprocess, os, re, threading

def console_print(host, prefix, output):
    """Print message to console"""
    if host and prefix:
        host = host + "[" + prefix + "]: "
    elif host and not prefix:
        host = host + ": "
    elif not host and prefix:
        host = os.path.basename(prefix) + ": "

    output = "[rsync-ssh] " + host + output.replace("\n", "\n[rsync-ssh] "+ host)
    print(output)

def console_show(window=sublime.active_window()):
    """Show console panel"""
    window.run_command("show_panel", {"panel": "console", "toggle": False})

def current_user():
    """Get current username from the environment"""
    if 'USER' in os.environ:
        return os.environ['USER']
    elif 'USERNAME' in os.environ:
        return os.environ['USERNAME']
    else:
        return 'username'

def check_output(*args, **kwargs):
    """Runs specified system command using subprocess.check_output()"""
    startupinfo = None
    if sublime.platform() == "windows":
        # Don't let console window pop-up on Windows.
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    return subprocess.check_output(*args, universal_newlines=True, startupinfo=startupinfo, **kwargs)

def rsync_ssh_settings(view=sublime.active_window().active_view()):
    """Get settings from the sublime project file"""
    project_data = view.window().project_data()

    # Not all windows have project data
    if project_data == None:
        return None

    settings = view.window().project_data().get('settings', {}).get("rsync_ssh")
    return settings


class RsyncSshInitSettingsCommand(sublime_plugin.TextCommand):
    """Sublime Command for creating the rsync_ssh block in the project settings file"""

    def run(self, edit, **args): # pylint: disable=W0613
        """Generate settings for rsync-ssh"""
        # Load project configuration
        project_data = self.view.window().project_data()

        if project_data == None:
            console_print("", "", "Unable to initialize settings, you must have a .sublime-project file.")
            console_print("", "", "Please use 'Project -> Save Project As...' first.")
            console_show(self.view.window())
            return

        # If no rsync-ssh config exists, then create it
        if not project_data.get('settings', {}).get("rsync_ssh"):
            if not project_data.get('settings'):
                project_data['settings'] = {}
            project_data['settings']["rsync_ssh"] = {}
            project_data['settings']["rsync_ssh"]["sync_on_save"] = True
            project_data['settings']["rsync_ssh"]["excludes"] = [
                '.git*', '_build', 'blib', 'Build'
            ]
            project_data['settings']["rsync_ssh"]["options"] = [
                "--dry-run",
                "--delete"
            ]
            # Add sane permission defaults when using windows (cygwin)
            if sublime.platform() == "windows":
                project_data['settings']["rsync_ssh"]["options"].insert(0, "--chmod=ugo=rwX")
                project_data['settings']["rsync_ssh"]["options"].insert(0, "--no-perms")

            project_data['settings']["rsync_ssh"]["remotes"] = {}

            if project_data.get("folders") == None:
                console_print("", "", "Unable to initialize settings, you must have at least one folder in your .sublime-project file.")
                console_print("", "", "Please use 'Add Folder to Project...' first.")
                console_show(self.view.window())
                return

            for folder in project_data.get("folders"):
                # Handle folder named '.'
                # User has added project file inside project folder, so we use the directory from the project file
                path = folder.get("path")
                if path == ".":
                    path = os.path.basename(os.path.dirname(self.view.window().project_file_name()))

                project_data['settings']["rsync_ssh"]["remotes"][path] = [{
                    "remote_host": "my-server.my-domain.tld",
                    "remote_path": "/home/" + current_user() + "/Projects/" + os.path.basename(path),
                    "remote_port": 22,
                    "remote_user": current_user(),
                    "remote_pre_command": "",
                    "remote_post_command": "",
                    "enabled": 1,
                    "options": [],
                    "excludes": []
                }]

            # Save configuration
            self.view.window().set_project_data(project_data)

        # We won't clobber an existing configuration
        else:
            console_print("", "", "rsync_ssh configuration already exists.")

        # Open configuration in new tab
        self.view.window().run_command("open_file", {"file": "${project}"})

class RsyncSshSyncBase(sublime_plugin.TextCommand):
    files                     = []
    hosts                     = []
    possibleRemotes           = []
    selectedRemoteKey         = None
    settings                  = False
    # Stuff to overwrite:
    identifier                = ''
    ignoreObviousRemoteChoice = False

    def run( self, edit, **args ): # pylint: disable=W0613
        self.files            = []
        self.hosts            = []
        self.possibleRemotes  = []

        projectData           = sublime.active_window().project_data()
        if projectData == None:
            self.settings     = False
            return

        self.settings         = projectData.get( 'settings', {} ).get( 'rsync_ssh', False )
    
    def sync_remote( self, choice ):
        """Call rsync_ssh_command with the selected remote"""

        if choice >= 0:
            self.view.settings().set( 'rsync_ssh_sync_' + self.identifier + '_remote', choice )

            self.selectedRemoteKey = self.possibleRemotes[ choice ]
            destinations = self.settings.get( 'remotes' ).get( self.selectedRemoteKey )

            # Remote has no destinations, which makes no sense
            if len( destinations ) == 0:
                self.view.window().status_message( 'Rsync: no destinations known...' )
                return
            # If remote only has one destination, we'll just initiate the sync
            elif len( destinations ) == 1 and not self.ignoreObviousRemoteChoice:
                # Start command thread to keep ui responsive
                self.view.run_command(
                    'rsync_ssh_sync', {
                        'force_sync': True,
                        'remote': self.selectedRemoteKey
                    }
                )
            else:
                self.hosts = [ [ 'All', 'Sync to all destinations' ] ]
                for destination in destinations:
                    self.hosts.append( [
                        destination.get( 'remote_user' ) + '@' + destination.get( 'remote_host' ) + ':' + str( destination.get( 'remote_port' ) ),
                        destination.get( 'remote_path' )
                    ] )

                selected_destination = self.view.settings().get( 'rsync_ssh_sync_' + self.identifier + '_destination', 0 )
                selected_destination = max( selected_destination, 0 )
                selected_destination = min( selected_destination, len( destinations ) )
                self.view.window().show_quick_panel( self.hosts, self.sync_destination, sublime.MONOSPACE_FONT, selected_destination )

    def sync_destination( self, choice ):
        """Sync single destination"""

        # 0 == All destinations > 0 == specific destination
        if choice >= 0:
            self.view.settings().set( 'rsync_ssh_sync_' + self.identifier + '_destination', choice )

            # Start command thread to keep ui responsive
            self.view.run_command(
                'rsync_ssh_sync', {
                    # When selecting a specific destination we'll force the sync
                    'force_sync': False if choice == 0 else True,
                    'remote': self.selectedRemoteKey,
                    'destination': choice,
                    'files': self.files
                }
            )

class RsyncSshSyncSelection(RsyncSshSyncBase):

    def run( self, edit, **args ): # pylint: disable=W0613
        self.identifier = 'file'
        self.ignoreObviousRemoteChoice = True

        super( RsyncSshSyncSelection, self ).run( edit, **args )
        if self.settings is False:
            return

        self.loadRemotePathMapping()

        # This method should be overwritten in subclass doing the 
        # matching and filling possible remotes to upload to
        if not self.setup( **args ):
            return

        if len( self.possibleRemotes ) == 0:
            console_print( '', '', 'No match found for this file/folder' )
            self.view.window().status_message( '### Rsync: No match found for this file/folder!' )
            return

        self.view.window().show_quick_panel( self.possibleRemotes, self.sync_remote, sublime.MONOSPACE_FONT, 0 )

    def setup( self, **args ):
        return False

    def loadRemotePathMapping( self ):
        self.realRemotePaths = {}
        windowFoldersArr = [ os.path.realpath( windowFolder ).strip( os.sep ).split( os.sep ) for windowFolder in self.view.window().folders() ]
        for remote in self.settings.get( 'remotes' ).keys():
            remoteArr = re.split( os.sep + '|/', remote.strip( os.sep ) )
            for windowFolderArr in windowFoldersArr:
                if len( windowFolderArr ) > 0 and len( remoteArr ) > 0 and windowFolderArr[ -1 ] == remoteArr[ 0 ]:
                    self.realRemotePaths[ remote ] = os.sep.join( [ '' ] + windowFolderArr + remoteArr[ 1: ] )

    def matchFile( self, file ):
        return [ remote for remote in self.realRemotePaths if self.isInDirectory( self.realRemotePaths[ remote ], file ) ]
    
    def isInDirectory( self, folder, file ):
        fileParts = os.path.realpath( file ).strip( os.sep ).split( os.sep )
        fileParts.reverse()
        folderParts = os.path.realpath( folder ).strip( os.sep ).split( os.sep )

        return not any( folderPart != fileParts.pop() for folderPart in folderParts )


class RsyncSshSyncSpecificRemoteCommand(RsyncSshSyncBase):
    """Start rsync for a specific remote"""

    def run(self, edit, **args): # pylint: disable=W0613
        """Let user select which remote/destination to sync using the quick panel"""

        self.identifier = 'specific_remote'
        self.ignoreObviousRemoteChoice = False

        super( RsyncSshSyncSpecificRemoteCommand, self ).run( edit, **args )
        if self.settings is False:
            return

        for remote_key in self.settings.get("remotes").keys():
            for destination in self.settings.get("remotes").get(remote_key):
                if destination.get("enabled", True) == True:
                    if remote_key not in self.possibleRemotes:
                        self.possibleRemotes.append(remote_key)

        selected_remote = self.view.settings().get("rsync_ssh_sync_" + self.identifier + "_remote", 0)
        self.view.window().show_quick_panel(self.possibleRemotes, self.sync_remote, sublime.MONOSPACE_FONT, selected_remote)

class RsyncSshSaveCommand(sublime_plugin.EventListener):
    """Sublime Command for syncing a single file when user saves"""

    def on_post_save(self, view):
        """Invoked each time the user saves a file."""

        # Get settings
        settings = rsync_ssh_settings(view)

        # Don't do anything if rsync-ssh hasn't been configured
        if not settings:
            return
        # Don't sync single file if user has disabled sync on save
        elif settings.get("sync_on_save", True) == False:
            return

        # TODO: review if it gets rewritten and reactivated
        print( '"Sync on save" is not supported by this version!' )
        return

        # Don't sync git commit message buffer
        if os.path.basename(view.file_name()) == "COMMIT_EDITMSG":
            return

        # Return if we are already syncing the file
        if view.get_status("00000_rsync_ssh_status"):
            if settings.get("debug", False) == True:
                print("Sync already in progress")
            return

        # Block other instances of the same file from initiating sync (e.g. files open in more than one view)
        view.set_status("00000_rsync_ssh_status", "Sync initiated")

        # Execute sync with the name of file being saved
        view.run_command("rsync_ssh_sync", {"path_being_saved": view.file_name()})

class RsyncSshSyncFileCommand(RsyncSshSyncSelection):
    """Start rsync for a specific remote"""

    def setup( self, **args ): # pylint: disable=W0613
        currentFile = self.view.file_name()
        if currentFile is None:
            return False

        self.files.append( currentFile )
        self.possibleRemotes = self.matchFile( currentFile )

        return True

class RsyncSshSideCommand(RsyncSshSyncSelection):

    def setup( self, **args ): # pylint: disable=W0613
        paths = args.get( 'paths', None )
        if paths is None or len( paths ) == 0:
            return False

        self.files.extend( paths )

        possibleRemotes = { path:self.matchFile( path ) for path in paths }
        iterator = iter( possibleRemotes )
        first = next( iterator, False )
        if not first or not all( possibleRemotes[ first ] == possibleRemotes[ rest ] for rest in iterator ):
            self.view.window().status_message( '### Rsync: Selection requests multiple locations!' )
            return False

        self.possibleRemotes = possibleRemotes[ first ];

        return True


class RsyncSshSyncCommand(RsyncSshSyncBase):
    """Sublime Command for invoking the actual sync process"""

    def run(self, edit, **args): # pylint: disable=W0613
        """Start thread with rsync to keep ui responsive"""

        super( RsyncSshSyncCommand, self ).run( edit, **args )
        if self.settings is False:
            return

        # Start command thread to keep ui responsive
        thread = RsyncSSH(
            self.view,
            self.settings,
            args.get("remote", None),
            args.get("destination", None),
            args.get("files", []),
            args.get("force_sync", False)
        )
        thread.start()


class RsyncSSH(threading.Thread):
    """Rsync path to remote"""

    view                     = None
    settings                 = None
    folder                   = None
    destination              = None
    files                    = []
    force_sync               = False
    threads                  = []
    realRemotePaths          = {}

    def __init__( self, view, settings, folder = None, destination = None, files = [], force_sync = False ):
        """Set the stage"""
        self.view            = view
        self.settings        = settings
        self.folder          = folder
        self.destination     = destination
        self.files           = files
        self.force_sync      = force_sync
        self.threads         = []
        
        # Get the project paths
        self.realRemotePaths = {}
        windowFoldersArr = [ os.path.realpath( windowFolder ).strip( os.sep ).split( os.sep ) for windowFolder in self.view.window().folders() ]
        for remote in self.settings.get( "remotes" ).keys():
            remoteArr = re.split( os.sep + '|/', remote.strip( os.sep ) )
            for windowFolderArr in windowFoldersArr:
                if len( windowFolderArr ) > 0 and len( remoteArr ) > 0 and windowFolderArr[ -1 ] == remoteArr[ 0 ]:
                    self.realRemotePaths[ remote ] = os.sep.join( [''] + windowFolderArr + remoteArr[1:] )


        threading.Thread.__init__(self)

    def run(self):
        """Iterate over remotes and destinations and sync all paths that match the saved path"""

        # Map destination index to the real data object
        # Irregularities -> None -> Loop over all
        if self.destination is not None:
            if self.folder is None:
                self.destination = None
            else:
                destinations = self.settings.get( "remotes" ).get( self.folder )
                # lower than w/o equal as first destination is 'All'
                if len( destinations ) < self.destination or self.destination <= 0:
                    self.destination = None
                else:
                    self.destination = destinations[ self.destination - 1 ]

        if self.folder is None:
            for folder in self.settings.get( "remotes" ).keys():
                self.runFolder( folder )
        else:
            self.runFolder()

        # Wait for all threads to finish
        if len( self.threads ) > 0:
            for thread in self.threads:
                thread.join()
            status_bar_message = self.view.get_status( "00000_rsync_ssh_status" )
            self.view.set_status( "00000_rsync_ssh_status", "" )
            sublime.status_message( status_bar_message + " - done." )
            console_print( "", "", "done" )
        else:
            status_bar_message = self.view.get_status( "00000_rsync_ssh_status" )
            self.view.set_status( "00000_rsync_ssh_status", "" )
            sublime.status_message( status_bar_message + " - done." )

        # Unblock sync
        self.view.set_status( "00000_rsync_ssh_status", "" )

    def runFolder( self, folder = None ):
        if folder is None:
            folder = self.folder

        if self.destination is None:
            for destination in self.settings.get( "remotes" ).get( folder ):
                self.runDestination( folder, destination )
            pass
        else:
            self.runDestination( folder, self.destination )

    def runDestination( self, folder, destination ):
        # Merge settings with defaults
        global_excludes = [".DS_Store"]
        global_excludes.extend( self.settings.get( "excludes", [] ) )

        global_options = []
        global_options.extend( self.settings.get( "options", [] ) )

        connect_timeout = self.settings.get( "timeout", 10 )

        # Get path to local ssh binary
        ssh_binary = self.settings.get( "ssh_binary", self.settings.get( "ssh_command", "ssh" ) )

        destination_string = ":".join( [
            destination.get( "remote_user" ) + "@" + destination.get( "remote_host" ),
            str( destination.get( "remote_port", 22 ) ),
            destination.get( "remote_path" )
        ] )

        # Merge local settings with global defaults
        local_excludes = list( global_excludes )
        local_excludes.extend( destination.get( "excludes", [] ) )

        local_options = list( global_options )
        local_options.extend( destination.get( "options", [] ) )

        if folder not in self.realRemotePaths:
            console_print( folder, 'is unknown' )
            return
        local_path = self.realRemotePaths[ folder ]

        threads = []
        if len( self.files ) > 0:
            for file in self.files:
                thread = Rsync(
                    self.view,
                    ssh_binary,
                    local_path,
                    folder, # old 'prefix' -- this is just for logging to console
                    destination,
                    local_excludes,
                    local_options,
                    connect_timeout,
                    file,
                    self.force_sync
                )
                threads.append( thread )
                self.threads.append( thread )
        else :
            thread = Rsync(
                self.view,
                ssh_binary,
                local_path,
                folder, # old 'prefix' -- this is just for logging to console
                destination,
                local_excludes,
                local_options,
                connect_timeout,
                None,
                self.force_sync
            )
            threads.append( thread )
            self.threads.append( thread )
        

        # Update status message
        status_bar_message = "Rsyncing to " + str( len( self.threads ) ) + " destination(s)"
        self.view.set_status( "00000_rsync_ssh_status", status_bar_message )

        for thread in threads:
            thread.start()

class Rsync(threading.Thread):
    """rsync executor"""

    def __init__(self, view, ssh_binary, local_path, prefix, destination, excludes, options, timeout, specific_path, force_sync=False):
        self.ssh_binary    = ssh_binary
        self.view          = view
        self.local_path    = local_path
        self.prefix        = prefix
        self.destination   = destination
        self.excludes      = excludes
        self.options       = options
        self.timeout       = timeout
        self.specific_path = specific_path
        self.force_sync    = force_sync
        self.rsync_path    = ''
        threading.Thread.__init__(self)

    def ssh_command_with_default_args(self):
        """Get ssh command with defaults"""

        # Build list with defaults
        ssh_command = [
            self.ssh_binary, "-q", "-T",
            "-o", "ConnectTimeout="+str(self.timeout)
        ]
        if self.destination.get("remote_port"):
            ssh_command.extend(["-p", str(self.destination.get("remote_port"))])

        return ssh_command

    def run(self):
        # Cygwin version of rsync is assumed on Windows. Local path needs to be converted using cygpath.
        if sublime.platform() == "windows":
            try:
                self.local_path = check_output(["cygpath", self.local_path]).strip()
                if self.specific_path:
                    self.specific_path = check_output(["cygpath", self.specific_path]).strip()
            except subprocess.CalledProcessError as error:
                console_show(self.view.window())
                console_print(
                    self.destination.get("remote_host"),
                    self.prefix,
                    "ERROR: Failed to run cygpath to convert local file path. Can't continue."
                )
                console_print(self.destination.get("remote_host"), self.prefix, error.output)
                return

        # Skip disabled destinations, unless we explicitly force a sync (e.g. for specific destinations)
        if not self.force_sync and not self.destination.get("enabled", 1):
            console_print(self.destination.get("remote_host"), self.prefix, "Skipping, destination is disabled.")
            return

        # What to rsync
        source_path      = self.local_path + "/"
        destination_path = self.destination.get("remote_path")

        # Handle specific path syncs (e.g. save events and specific remote)
        if self.specific_path and os.path.isfile(self.specific_path) and self.specific_path.startswith(self.local_path+"/"):
            source_path      = self.specific_path
            destination_path = self.destination.get("remote_path") + self.specific_path.replace(self.local_path, "")
        elif self.specific_path and os.path.isdir(self.specific_path) and self.specific_path.startswith(self.local_path+"/"):
            source_path      = self.specific_path + "/"
            destination_path = self.destination.get("remote_path") + self.specific_path.replace(self.local_path, "")

        # Check ssh connection, and get path of rsync on the remote host
        check_command = self.ssh_command_with_default_args()
        check_command.extend([
            self.destination.get("remote_user")+"@"+self.destination.get("remote_host"),
            "LANG=C which rsync"
        ])
        try:
            self.rsync_path = check_output(check_command, timeout=self.timeout, stderr=subprocess.STDOUT).rstrip()
            if not self.rsync_path.endswith("/rsync"):
                console_show(self.view.window())
                message = "ERROR: Unable to locate rsync on "+self.destination.get("remote_host")
                console_print(self.destination.get("remote_host"), self.prefix, message)
                console_print(self.destination.get("remote_host"), self.prefix, self.rsync_path)
                return
        except subprocess.TimeoutExpired as error:
            console_show(self.view.window())
            console_print(self.destination.get("remote_host"), self.prefix, "ERROR: "+error.output)
            return
        except subprocess.CalledProcessError as error:
            console_show(self.view.window())
            if error.returncode == 255 and error.output == '':
                console_print(self.destination.get("remote_host"), self.prefix, "ERROR: ssh check command failed, have you accepted the remote host key?")
                console_print(self.destination.get("remote_host"), self.prefix, "       Try running the ssh command manually in a terminal:")
                console_print(self.destination.get("remote_host"), self.prefix, "       "+" ".join(error.cmd))
            else:
                console_print(self.destination.get("remote_host"), self.prefix, "ERROR: "+error.output)

            return

        # Remote pre command
        if self.destination.get("remote_pre_command"):
            pre_command = self.ssh_command_with_default_args()
            pre_command.extend([
                self.destination.get("remote_user")+"@"+self.destination.get("remote_host"),
                "$SHELL -l -c \"LANG=C cd "+self.destination.get("remote_path")+" && "+self.destination.get("remote_pre_command")+"\""
            ])
            try:
                console_print(self.destination.get("remote_host"), self.prefix, "Running pre command: "+self.destination.get("remote_pre_command"))
                output = check_output(pre_command, stderr=subprocess.STDOUT)
                if output:
                    output = re.sub(r'\n$', "", output)
                    console_print(self.destination.get("remote_host"), self.prefix, output)
            except subprocess.CalledProcessError as error:
                console_show(self.view.window())
                console_print(self.destination.get("remote_host"), self.prefix, "ERROR: "+error.output+"\n")

        # Build rsync command
        rsync_command = [
            "rsync", "-v", "-zar",
            "-e", " ".join(self.ssh_command_with_default_args())
        ]

        # We allow options to be specified as "--foo bar" in the config so we need to split all options on first space after the option name
        for option in self.options:
            rsync_command.extend( option.split(" ", 1) )

        rsync_command.extend([
            source_path,
            self.destination.get("remote_user")+"@"+self.destination.get("remote_host")+":'"+destination_path+"'"
        ])

        # Add excludes
        for exclude in set(self.excludes):
            rsync_command.append("--exclude="+exclude)

        # Show actual rsync command in the console
        console_print(self.destination.get("remote_host"), self.prefix, " ".join(rsync_command))

        # Add mkdir unless we have a --dry-run flag
        if  len([option for option in rsync_command if '--dry-run' in option]) == 0:
            rsync_command.extend([
                "--rsync-path",
                "mkdir -p '" + os.path.dirname(destination_path) + "' && " + self.rsync_path
            ])

        # Execute rsync
        try:
            output = check_output(rsync_command, stderr=subprocess.STDOUT)
            # Fix rsync output to include relative remote path
            if self.specific_path and os.path.isfile(self.specific_path):
                destination_file_relative = re.sub(self.destination.get("remote_path")+'/?', '', destination_path)
                destination_file_basename = os.path.basename(destination_file_relative)
                output = re.sub(destination_file_basename, destination_file_relative, output)
            console_print(self.destination.get("remote_host"), self.prefix, output)
            if  len([option for option in rsync_command if '--dry-run' in option]) != 0:
                console_print(self.destination.get("remote_host"), self.prefix, "NOTICE: Nothing synced. Remove --dry-run from options to sync.")
        except subprocess.CalledProcessError as error:
            console_show(self.view.window())
            if  len([option for option in rsync_command if '--dry-run' in option]) != 0 and re.search("No such file or directory", error.output, re.MULTILINE):
                console_print(
                    self.destination.get("remote_host"), self.prefix,
                    "WARNING: Unable to do dry run, remote directory "+os.path.dirname(destination_path)+" does not exist."
                )
            else:
                console_print(self.destination.get("remote_host"), self.prefix, "ERROR: "+error.output+"\n")

        # Remote post command
        if self.destination.get("remote_post_command"):
            post_command = self.ssh_command_with_default_args()
            post_command.extend([
                self.destination.get("remote_user")+"@"+self.destination.get("remote_host"),
                "$SHELL -l -c \"LANG=C cd \\\""+self.destination.get("remote_path")+"\\\" && "+self.destination.get("remote_post_command")+"\""
            ])
            try:
                console_print(self.destination.get("remote_host"), self.prefix, "Running post command: "+self.destination.get("remote_post_command"))
                output = check_output(post_command, stdin=subprocess.DEVNULL, stderr=subprocess.STDOUT)
                if output:
                    output = re.sub(r'\n$', "", output)
                    console_print(self.destination.get("remote_host"), self.prefix, output)
            except subprocess.CalledProcessError as error:
                console_show(self.view.window())
                console_print(self.destination.get("remote_host"), self.prefix, "ERROR: "+error.output+"\n")

        # End of run
        return
