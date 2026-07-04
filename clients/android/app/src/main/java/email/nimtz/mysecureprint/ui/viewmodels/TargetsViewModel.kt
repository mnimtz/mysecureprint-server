package email.nimtz.mysecureprint.ui.viewmodels

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import email.nimtz.mysecureprint.data.api.ApiClient
import email.nimtz.mysecureprint.data.api.ApiResult
import email.nimtz.mysecureprint.data.model.PrintTarget
import email.nimtz.mysecureprint.data.model.TargetsResponse
import email.nimtz.mysecureprint.data.store.SettingsStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

sealed class TargetsUiState {
    object Loading : TargetsUiState()
    data class Success(val queues: List<PrintTarget>) : TargetsUiState()
    data class Error(val message: String) : TargetsUiState()
}

class TargetsViewModel(private val settings: SettingsStore) : ViewModel() {

    private val _uiState = MutableStateFlow<TargetsUiState>(TargetsUiState.Loading)
    val uiState: StateFlow<TargetsUiState> = _uiState

    init { load() }

    fun load() {
        viewModelScope.launch {
            _uiState.value = TargetsUiState.Loading
            val client = ApiClient(settings.serverUrl, settings.authToken)
            val raw = client.getTargets()
            when (val result = client.parseBody<TargetsResponse>(raw)) {
                is ApiResult.Success      -> _uiState.value = TargetsUiState.Success(result.body.queues)
                is ApiResult.Error        -> _uiState.value = TargetsUiState.Error("HTTP ${result.code}: ${result.message}")
                is ApiResult.NetworkError -> _uiState.value = TargetsUiState.Error(result.message)
            }
        }
    }

    fun setDefaultQueue(queue: PrintTarget) {
        settings.defaultQueueId   = queue.id
        settings.defaultQueueName = queue.name
    }
}

class TargetsViewModelFactory(private val settings: SettingsStore) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T = TargetsViewModel(settings) as T
}
