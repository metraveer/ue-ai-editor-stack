import datetime
import os

import unreal

import toolset_registry
from ai_editor_stack import sketchfab_api as api


@unreal.ustruct()
class SketchfabModel(unreal.StructBase):
    """Summary of a Sketchfab model."""
    uid = unreal.uproperty(str)
    name = unreal.uproperty(str)
    author = unreal.uproperty(str)
    author_url = unreal.uproperty(str)
    license_slug = unreal.uproperty(str, meta={'ToolTip': 'cc0, by, by-sa, by-nd, by-nc, by-nc-sa, by-nc-nd, free-st, st or ed'})
    license_label = unreal.uproperty(str)
    face_count = unreal.uproperty(int)
    animation_count = unreal.uproperty(int)
    glb_size_mb = unreal.uproperty(int, meta={'ToolTip': 'GLB download size in MB, rounded up; 0 when unknown'})
    texture_max_resolution = unreal.uproperty(int, meta={'ToolTip': 'Largest texture side in pixels; 0 when unknown'})
    url = unreal.uproperty(str, meta={'ToolTip': 'Model page on sketchfab.com'})
    thumbnail_url = unreal.uproperty(str)


@unreal.ustruct()
class SketchfabSearchPage(unreal.StructBase):
    """One page of Sketchfab search results."""
    models = unreal.uproperty(unreal.Array(SketchfabModel))
    next_cursor = unreal.uproperty(str, meta={'ToolTip': 'Cursor for the next page; empty on the last page'})


@unreal.ustruct()
class SketchfabModelDetails(unreal.StructBase):
    """Full information about one Sketchfab model, including its license terms."""
    model = unreal.uproperty(SketchfabModel)
    description = unreal.uproperty(str)
    tags = unreal.uproperty(unreal.Array(str))
    vertex_count = unreal.uproperty(int)
    material_count = unreal.uproperty(int)
    texture_count = unreal.uproperty(int)
    downloadable = unreal.uproperty(bool)
    commercial_use_allowed = unreal.uproperty(bool, meta={'ToolTip': 'False for NonCommercial, NoDerivs and Editorial licenses'})
    license_requirements = unreal.uproperty(str)
    license_url = unreal.uproperty(str)


@unreal.ustruct()
class SketchfabImportResult(unreal.StructBase):
    """Outcome of importing a Sketchfab model into the project."""
    uid = unreal.uproperty(str)
    name = unreal.uproperty(str)
    destination_path = unreal.uproperty(str, meta={'ToolTip': 'Content folder holding the imported assets'})
    imported_assets = unreal.uproperty(unreal.Array(str), meta={'ToolTip': 'Object paths of the imported assets'})
    attribution = unreal.uproperty(str, meta={'ToolTip': 'Credit line required by the license'})
    credits_file = unreal.uproperty(str, meta={'ToolTip': 'Absolute path of the project credits file'})
    warnings = unreal.uproperty(unreal.Array(str), meta={
        'ToolTip': 'Problems found after the import: empty meshes that were deleted, unlit materials, ...'})


@unreal.ustruct()
class SketchfabAccount(unreal.StructBase):
    """The Sketchfab account of the configured API token."""
    username = unreal.uproperty(str)
    token_source = unreal.uproperty(str, meta={'ToolTip': 'Where the token was read from (never the token itself)'})


@unreal.uclass()
class SketchfabTools(unreal.ToolsetDefinition):
    """Finds free downloadable 3D models on Sketchfab and imports them into the project as glTF via Interchange,
    tagging the assets and recording author/license credits. Downloading needs a Sketchfab API token on this PC,
    which the user enters in a desktop window this toolset opens."""

    @toolset_registry.tool_call
    @staticmethod
    def search(query: str, commercial_use_only: bool = True, license_slug: str | None = None, max_face_count: int = 0,
               animated_only: bool = False, sort: str = 'relevance', count: int = 12,
               cursor: str | None = None) -> SketchfabSearchPage:
        """Searches downloadable Sketchfab models.

        Args:
            query: Search words.
            commercial_use_only: Hide NonCommercial, NoDerivs and Editorial licenses; the page may then hold fewer than count models.
            license_slug: Only this license, e.g. "cc0" or "by". Omit for any.
            max_face_count: Triangle limit; 0 for none.
            animated_only: Only models with animations.
            sort: "relevance", "likes", "views" or "newest".
            count: Models per page, 1-24.
            cursor: next_cursor of the previous page.

        Returns:
            A page of models.
        """
        return SketchfabTools._search(query, commercial_use_only, license_slug, max_face_count, animated_only,
                                      sort, count, cursor)

    @toolset_registry.tool_call
    @staticmethod
    def get_model(uid: str) -> SketchfabModelDetails:
        """Returns full details and license terms of a model.

        Args:
            uid: Sketchfab model uid.

        Returns:
            The model details.
        """
        return SketchfabTools._details(uid)

    @toolset_registry.tool_call
    @staticmethod
    def import_model(uid: str, destination_folder: str = '/Game/Sketchfab', allow_restricted_license: bool = False,
                     max_download_mb: float = 500.0) -> SketchfabImportResult:
        """Downloads a model (GLB, or zipped glTF) and imports it into its own sub-folder of destination_folder,
        tags the assets with Sketchfab.* metadata and appends an attribution line to <Project>/Credits/Sketchfab.md.
        The editor is blocked while downloading; a cancellable progress dialog is shown.

        Args:
            uid: Sketchfab model uid.
            destination_folder: A /Game/... content folder.
            allow_restricted_license: Required for NonCommercial, NoDerivs and Editorial licenses. Set only after the user has seen the license terms and agreed.
            max_download_mb: Refuse larger downloads.

        Returns:
            What was imported and the credit line.
        """
        token, _ = api.get_token()
        if not token:
            raise RuntimeError(api.no_token_message())
        if not destination_folder.startswith('/Game'):
            raise ValueError('destination_folder must be a /Game/... content path.')
        details = SketchfabTools._details(uid)
        model = details.model
        if not details.downloadable:
            raise ValueError(f'"{model.name}" is not downloadable.')
        if api.is_restricted(model.license_slug) and not allow_restricted_license:
            raise PermissionError(
                f'"{model.name}" uses {model.license_label}: {details.license_requirements} '
                'Show this to the user and retry with allow_restricted_license=true only if they agree.')

        links = api.request_json(f'/models/{model.uid}/download', token=token)
        fmt = 'glb' if 'glb' in links else ('gltf' if 'gltf' in links else '')
        if not fmt:
            raise RuntimeError(f'No glTF/GLB download for this model (offered: {", ".join(links) or "nothing"}).')
        size_mb = (links[fmt].get('size') or 0) / 1048576
        if size_mb > max_download_mb:
            raise ValueError(f'Download is {size_mb:.0f} MB, above max_download_mb={max_download_mb:.0f}.')

        safe = api.safe_name(model.name)
        work = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()),
                            'Sketchfab', model.uid)
        os.makedirs(work, exist_ok=True)
        archive = os.path.join(work, safe + ('.glb' if fmt == 'glb' else '.zip'))
        SketchfabTools._download(links[fmt]['url'], archive, f'Sketchfab: {model.name}')
        source = archive if fmt == 'glb' else api.extract_gltf(archive, os.path.join(work, 'gltf'))

        destination = f'{destination_folder.rstrip("/")}/{safe}_{model.uid[:6]}'
        imported = SketchfabTools._import(source, destination)
        imported, warnings = SketchfabTools._inspect(imported)
        SketchfabTools._tag(imported, model)

        result = SketchfabImportResult()
        result.uid = model.uid
        result.name = model.name
        result.destination_path = destination
        result.imported_assets = imported
        result.warnings = warnings
        result.attribution = (
            f'"{model.name}" ({model.url}) by {model.author} ({model.author_url}) is licensed under '
            f'{model.license_label} ({details.license_url or "see model page"}).')
        project = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
        result.credits_file = SketchfabTools._write_credits(
            os.path.join(project, 'Credits', 'Sketchfab.md'), model.uid,
            f'{result.attribution} Imported to `{destination}` on {datetime.date.today().isoformat()}.')
        return result

    @toolset_registry.tool_call
    @staticmethod
    def get_account() -> SketchfabAccount:
        """Verifies the configured Sketchfab API token.

        Returns:
            The account the token belongs to.
        """
        token, source = api.get_token()
        if not token:
            raise RuntimeError(api.no_token_message())
        me = api.request_json('/me', token=token)
        account = SketchfabAccount()
        account.username = me.get('username', '')
        account.token_source = source
        return account

    @toolset_registry.tool_call
    @staticmethod
    def open_token_dialog() -> None:
        """Opens a window on the user's desktop where they paste their Sketchfab API token; the window verifies it
        with Sketchfab and saves it on this PC, so the token never passes through the chat. Returns without waiting
        for the user."""
        api.open_token_dialog()

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _search(query, commercial_use_only, license_slug, max_face_count, animated_only, sort, count, cursor):
        sort_by = {'relevance': '', 'likes': '-likeCount', 'views': '-viewCount', 'newest': '-publishedAt'}
        if not query.strip():
            raise ValueError('query must not be empty.')
        if sort not in sort_by:
            raise ValueError(f'sort must be one of {", ".join(sort_by)}.')
        if not 1 <= count <= 24:
            raise ValueError('count must be between 1 and 24.')
        data = api.request_json('/search', {
            'type': 'models', 'q': query, 'downloadable': 'true', 'count': str(count), 'cursor': cursor or '',
            'license': license_slug or '', 'sort_by': sort_by[sort],
            'max_face_count': str(max_face_count) if max_face_count > 0 else '',
            'animated': 'true' if animated_only else ''})
        slugs = api.license_slugs()
        models = [SketchfabTools._summary(m, slugs) for m in data.get('results', [])]
        page = SketchfabSearchPage()
        page.models = [m for m in models if not (commercial_use_only and api.is_restricted(m.license_slug))]
        page.next_cursor = str((data.get('cursors') or {}).get('next') or '')
        return page

    @staticmethod
    def _details(uid):
        data = api.request_json(f'/models/{api.check_uid(uid)}')
        lic = data.get('license') or {}
        details = SketchfabModelDetails()
        details.model = SketchfabTools._summary(data, api.license_slugs())
        details.description = (data.get('description') or '')[:2000]
        details.tags = [t.get('name', '') for t in data.get('tags') or []]
        details.vertex_count = int(data.get('vertexCount') or 0)
        details.material_count = int(data.get('materialCount') or 0)
        details.texture_count = int(data.get('textureCount') or 0)
        details.downloadable = bool(data.get('isDownloadable'))
        details.commercial_use_allowed = not api.is_restricted(details.model.license_slug)
        details.license_requirements = lic.get('requirements', '')
        details.license_url = lic.get('url', '')
        return details

    @staticmethod
    def _summary(data, slugs):
        lic = data.get('license') or {}
        user = data.get('user') or {}
        glb = (data.get('archives') or {}).get('glb') or {}
        images = (data.get('thumbnails') or {}).get('images') or []
        medium = [i for i in images if 200 <= (i.get('width') or 0) <= 1024] or images
        model = SketchfabModel()
        model.uid = data.get('uid', '')
        model.name = data.get('name', '')
        model.author = user.get('displayName') or user.get('username') or ''
        model.author_url = user.get('profileUrl', '')
        model.license_slug = api.slug_of(lic, slugs)
        model.license_label = lic.get('label', '')
        model.face_count = int(data.get('faceCount') or 0)
        model.animation_count = int(data.get('animationCount') or 0)
        model.glb_size_mb = -(-(glb.get('size') or 0) // 1048576)
        model.texture_max_resolution = int(glb.get('textureMaxResolution') or 0)
        model.url = data.get('viewerUrl', '')
        model.thumbnail_url = max(medium, key=lambda i: i.get('width') or 0).get('url', '') if medium else ''
        return model

    @staticmethod
    def _download(url, dest, title):
        with unreal.ScopedSlowTask(100, title) as task:
            task.make_dialog(True)
            shown = [0]

            def progress(_chunk, done, total):
                if task.should_cancel():
                    raise RuntimeError('Download cancelled by the user.')
                percent = int(done * 100 / total) if total else 0
                if percent > shown[0]:
                    task.enter_progress_frame(percent - shown[0],
                                              f'{title} ({done / 1048576:.0f} / {total / 1048576:.0f} MB)')
                    shown[0] = percent

            api.download_file(url, dest, progress)

    @staticmethod
    def _import(source, destination):
        task = unreal.AssetImportTask()
        task.set_editor_property('filename', source)
        task.set_editor_property('destination_path', destination)
        task.set_editor_property('automated', True)
        task.set_editor_property('replace_existing', True)
        task.set_editor_property('save', True)
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        paths = [str(p) for p in task.get_editor_property('imported_object_paths')]
        if not paths:
            paths = [str(p) for p in unreal.EditorAssetLibrary.list_assets(destination, recursive=True, include_folder=False)]
        if not paths:
            raise RuntimeError(f'Interchange imported nothing from {source}; see the Output Log.')
        return paths

    @staticmethod
    def _inspect(paths):
        """Drops static meshes without geometry (some scans import an empty node mesh) and flags unlit materials
        (glTF KHR_materials_unlit), which ignore scene lights. Returns (remaining paths, warnings)."""
        keep, warnings = [], []
        for path in paths:
            asset = unreal.EditorAssetLibrary.load_asset(path)
            if isinstance(asset, unreal.StaticMesh) and asset.get_num_vertices(0) == 0:
                name = asset.get_name()
                if unreal.EditorAssetLibrary.delete_asset(path.split('.')[0]):
                    warnings.append(f'Deleted the empty static mesh {name}: the file has no geometry for it.')
                    continue
                warnings.append(f'Static mesh {name} has no geometry.')
            if isinstance(asset, unreal.MaterialInstance):
                parent = asset.get_editor_property('parent')
                if parent and 'unlit' in parent.get_name().lower():
                    warnings.append(
                        f'Material {asset.get_name()} is unlit (glTF KHR_materials_unlit): it ignores scene lights and '
                        'shows its texture at full brightness. For a model that must react to light, make a child of '
                        'the lit glTF material M_GLTF with the same BaseColorTexture and assign that.')
            keep.append(path)
        return keep, warnings

    @staticmethod
    def _tag(paths, model):
        tags = {'Sketchfab.Uid': model.uid, 'Sketchfab.Author': model.author,
                'Sketchfab.License': model.license_label, 'Sketchfab.Url': model.url}
        for path in paths:
            asset = unreal.EditorAssetLibrary.load_asset(path)
            if not asset:
                continue
            for key, value in tags.items():
                unreal.EditorAssetLibrary.set_metadata_tag(asset, key, value)
            unreal.EditorAssetLibrary.save_loaded_asset(asset, False)

    @staticmethod
    def _write_credits(path, uid, line):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path):
            with open(path, encoding='utf-8') as f:
                lines = f.read().splitlines()
        else:
            lines = ['# Sketchfab credits', '',
                     'Third-party models imported from Sketchfab. Keep this file with the project and credit the '
                     'authors wherever their licenses require it.', '']
        marker = f'<!-- {uid} -->'
        lines = [line_ for line_ in lines if marker not in line_] + [f'- {line} {marker}']
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(lines) + '\n')
        return path
