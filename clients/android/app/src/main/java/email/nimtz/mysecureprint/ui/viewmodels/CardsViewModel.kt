package email.nimtz.mysecureprint.ui.viewmodels

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import email.nimtz.mysecureprint.data.api.ApiClient
import email.nimtz.mysecureprint.data.api.ApiResult
import email.nimtz.mysecureprint.data.model.CardInfo
import email.nimtz.mysecureprint.data.model.CardsResponse
import email.nimtz.mysecureprint.data.model.OkResponse
import email.nimtz.mysecureprint.data.store.SettingsStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

sealed class CardsUiState {
    object Loading : CardsUiState()
    data class Success(val cards: List<CardInfo>) : CardsUiState()
    data class Error(val message: String) : CardsUiState()
}

class CardsViewModel(private val settings: SettingsStore) : ViewModel() {

    private val _uiState = MutableStateFlow<CardsUiState>(CardsUiState.Loading)
    val uiState: StateFlow<CardsUiState> = _uiState

    private val _actionResult = MutableStateFlow<String>("")
    val actionResult: StateFlow<String> = _actionResult

    init { load() }

    fun load() {
        viewModelScope.launch {
            _uiState.value = CardsUiState.Loading
            val client = ApiClient(settings.serverUrl, settings.authToken)
            val raw = client.getCards()
            when (val result = client.parseBody<CardsResponse>(raw)) {
                is ApiResult.Success      -> _uiState.value = CardsUiState.Success(result.body.cards)
                is ApiResult.Error        -> _uiState.value = CardsUiState.Error("HTTP ${result.code}: ${result.message}")
                is ApiResult.NetworkError -> _uiState.value = CardsUiState.Error(result.message)
            }
        }
    }

    fun registerCard(uid: String) {
        viewModelScope.launch {
            val client = ApiClient(settings.serverUrl, settings.authToken)
            val raw = client.registerCard(uid)
            when (val result = client.parseBody<OkResponse>(raw)) {
                is ApiResult.Success      -> { _actionResult.value = "Karte registriert!"; load() }
                is ApiResult.Error        -> _actionResult.value = "Fehler: ${result.message}"
                is ApiResult.NetworkError -> _actionResult.value = result.message
            }
        }
    }

    fun deleteCard(cardId: String) {
        viewModelScope.launch {
            val client = ApiClient(settings.serverUrl, settings.authToken)
            client.deleteCard(cardId)
            load()
        }
    }

    fun clearActionResult() { _actionResult.value = "" }
}

class CardsViewModelFactory(private val settings: SettingsStore) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T = CardsViewModel(settings) as T
}
