package email.nimtz.mysecureprint.ui.viewmodels

import android.content.Context
import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import email.nimtz.mysecureprint.data.api.ApiClient
import email.nimtz.mysecureprint.data.api.ApiResult
import email.nimtz.mysecureprint.data.model.OkResponse
import email.nimtz.mysecureprint.data.store.SettingsStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

sealed class UploadUiState {
    object Idle : UploadUiState()
    data class Uploading(val progress: Int) : UploadUiState()
    object Success : UploadUiState()
    data class Error(val message: String) : UploadUiState()
}

class UploadViewModel(private val settings: SettingsStore) : ViewModel() {

    private val _uiState = MutableStateFlow<UploadUiState>(UploadUiState.Idle)
    val uiState: StateFlow<UploadUiState> = _uiState

    fun resetState() { _uiState.value = UploadUiState.Idle }

    fun upload(context: Context, uri: Uri, targetId: String = settings.defaultQueueId.ifBlank { "print:self" }) {
        viewModelScope.launch {
            _uiState.value = UploadUiState.Uploading(0)
            try {
                val contentResolver = context.contentResolver
                val mimeType = contentResolver.getType(uri) ?: "application/octet-stream"
                val filename  = resolveFilename(context, uri)

                val inputStream = contentResolver.openInputStream(uri)
                    ?: run { _uiState.value = UploadUiState.Error("Datei konnte nicht geöffnet werden."); return@launch }

                val client = ApiClient(settings.serverUrl, settings.authToken)
                val raw = client.uploadFile(
                    inputStream = inputStream,
                    filename    = filename,
                    mimeType    = mimeType,
                    targetId    = targetId,
                )
                inputStream.close()

                when (val result = client.parseBody<OkResponse>(raw)) {
                    is ApiResult.Success      -> _uiState.value = UploadUiState.Success
                    is ApiResult.Error        -> _uiState.value = UploadUiState.Error("HTTP ${result.code}: ${result.message}")
                    is ApiResult.NetworkError -> _uiState.value = UploadUiState.Error(result.message)
                }
            } catch (e: Exception) {
                _uiState.value = UploadUiState.Error(e.message ?: "Unbekannter Fehler")
            }
        }
    }

    private fun resolveFilename(context: Context, uri: Uri): String {
        var name = ""
        context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) {
                val col = cursor.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
                if (col >= 0) name = cursor.getString(col) ?: ""
            }
        }
        return name.ifBlank { uri.lastPathSegment ?: "document.pdf" }
    }
}

class UploadViewModelFactory(private val settings: SettingsStore) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T = UploadViewModel(settings) as T
}
