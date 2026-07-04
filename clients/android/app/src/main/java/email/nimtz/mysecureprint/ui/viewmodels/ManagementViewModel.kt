package email.nimtz.mysecureprint.ui.viewmodels

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import email.nimtz.mysecureprint.data.api.ApiClient
import email.nimtz.mysecureprint.data.api.ApiResult
import email.nimtz.mysecureprint.data.model.ManagedUser
import email.nimtz.mysecureprint.data.model.UsersResponse
import email.nimtz.mysecureprint.data.store.SettingsStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

sealed class ManagementUiState {
    object Loading : ManagementUiState()
    data class Success(val users: List<ManagedUser>) : ManagementUiState()
    data class Error(val message: String) : ManagementUiState()
}

class ManagementViewModel(private val settings: SettingsStore) : ViewModel() {

    private val _uiState = MutableStateFlow<ManagementUiState>(ManagementUiState.Loading)
    val uiState: StateFlow<ManagementUiState> = _uiState

    init { load() }

    fun load() {
        viewModelScope.launch {
            _uiState.value = ManagementUiState.Loading
            val client = ApiClient(settings.serverUrl, settings.authToken)
            val raw = client.getUsers()
            when (val result = client.parseBody<UsersResponse>(raw)) {
                is ApiResult.Success      -> _uiState.value = ManagementUiState.Success(result.body.users)
                is ApiResult.Error        -> _uiState.value = ManagementUiState.Error("HTTP ${result.code}: ${result.message}")
                is ApiResult.NetworkError -> _uiState.value = ManagementUiState.Error(result.message)
            }
        }
    }
}

class ManagementViewModelFactory(private val settings: SettingsStore) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T = ManagementViewModel(settings) as T
}
