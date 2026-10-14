import discord
from discord.ui import View, Button, Select
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from player import GuildPlayer


class FranchinoControlsView(View):
    """Pannello di controllo interattivo con pulsanti e menu Effetti DJ per la chat di Discord."""

    def __init__(self, player: "GuildPlayer"):
        super().__init__(timeout=None)  # Pulsanti persistenti durante la riproduzione
        self.player = player
        self._update_button_states()

    def _update_button_states(self):
        """Aggiorna lo stato visivo (etichette, colori) dei pulsanti in base al player."""
        is_paused = self.player.is_paused()
        self.pause_resume_btn.label = "Riprendi" if is_paused else "Pausa"
        self.pause_resume_btn.emoji = "▶️" if is_paused else "⏸️"
        self.pause_resume_btn.style = discord.ButtonStyle.success if is_paused else discord.ButtonStyle.secondary

        # Stato del Loop
        loop_mode = self.player.loop_mode
        if loop_mode == "song":
            self.loop_btn.label = "Loop: Traccia"
            self.loop_btn.style = discord.ButtonStyle.primary
        elif loop_mode == "queue":
            self.loop_btn.label = "Loop: Coda"
            self.loop_btn.style = discord.ButtonStyle.success
        else:
            self.loop_btn.label = "Loop: No"
            self.loop_btn.style = discord.ButtonStyle.secondary

        # Stato della Radio AutoPlay
        if getattr(self.player, "autoplay", False):
            self.radio_btn.label = "Radio: ON"
            self.radio_btn.style = discord.ButtonStyle.success
        else:
            self.radio_btn.label = "Radio: OFF"
            self.radio_btn.style = discord.ButtonStyle.secondary

        # Aggiorna selezione nel menu Effetti DJ
        current_effect = getattr(self.player, "audio_effect", "off")
        for opt in self.effect_select.options:
            opt.default = (opt.value == current_effect)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Verifica che l'utente che clicca il pulsante sia nel canale vocale con Franchino."""
        if not interaction.user:
            return True

        bot_vc = interaction.guild.voice_client if interaction.guild else None
        if bot_vc and bot_vc.channel:
            in_bot_channel = interaction.user.id in bot_vc.channel.voice_states
            if not in_bot_channel and isinstance(interaction.user, discord.Member) and interaction.user.voice:
                in_bot_channel = (interaction.user.voice.channel == bot_vc.channel)

            if not in_bot_channel:
                await interaction.response.send_message(
                    f"⚠️ Devi essere nel canale vocale **{bot_vc.channel.name}** per controllare Franchino!",
                    ephemeral=True
                )
                return False

        return True

    # ==========================================================================
    # RIGA 0: Controlli Riproduzione Principali
    # ==========================================================================
    @discord.ui.button(emoji="⏸️", label="Pausa", style=discord.ButtonStyle.secondary, row=0)
    async def pause_resume_btn(self, interaction: discord.Interaction, button: Button):
        """Mette in pausa o riprende la riproduzione."""
        if self.player.is_paused():
            self.player.resume()
            self._update_button_states()
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("▶️ Franchino riparte a suonare!", ephemeral=True)
        elif self.player.is_playing():
            self.player.pause()
            self._update_button_states()
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("⏸️ Musica in pausa!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nessun brano in riproduzione al momento.", ephemeral=True)

    @discord.ui.button(emoji="⏭️", label="Salta", style=discord.ButtonStyle.primary, row=0)
    async def skip_btn(self, interaction: discord.Interaction, button: Button):
        """Salta alla traccia successiva."""
        if not self.player.current:
            await interaction.response.send_message("❌ Nessun brano da saltare.", ephemeral=True)
            return

        skipped_title = self.player.current.title
        self.player.skip()
        await interaction.response.send_message(f"⏭️ Brano saltato: **{skipped_title}**", ephemeral=False)

    @discord.ui.button(emoji="⏹️", label="Stop", style=discord.ButtonStyle.danger, row=0)
    async def stop_btn(self, interaction: discord.Interaction, button: Button):
        """Ferma la musica e svuota la coda in silenzio assoluto."""
        self.player.stop()
        self.stop()
        if not interaction.response.is_done():
            await interaction.response.defer()

    @discord.ui.button(emoji="🔁", label="Loop: No", style=discord.ButtonStyle.secondary, row=0)
    async def loop_btn(self, interaction: discord.Interaction, button: Button):
        """Cicla tra le modalità loop: off -> song -> queue -> off."""
        current_mode = self.player.loop_mode
        if current_mode == "off":
            self.player.loop_mode = "song"
            mode_name = "Traccia singola 🔂"
        elif current_mode == "song":
            self.player.loop_mode = "queue"
            mode_name = "Tutta la coda 🔁"
        else:
            self.player.loop_mode = "off"
            mode_name = "Disattivato ➡️"

        self._update_button_states()
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"🔁 Modalità Loop impostata su: **{mode_name}**", ephemeral=True)

    @discord.ui.button(emoji="🔀", label="Mischia", style=discord.ButtonStyle.secondary, row=0)
    async def shuffle_btn(self, interaction: discord.Interaction, button: Button):
        """Mischia i brani in coda."""
        if len(self.player.queue) < 2:
            await interaction.response.send_message("⚠️ Servono almeno 2 brani in coda per mischiare!", ephemeral=True)
            return

        self.player.shuffle()
        await interaction.response.send_message(f"🔀 Coda mescolata! ({len(self.player.queue)} brani)", ephemeral=False)

    # ==========================================================================
    # RIGA 1: Volume, Radio AutoPlay, Salva Preferiti, Coda
    # ==========================================================================
    @discord.ui.button(emoji="🔉", label="-10%", style=discord.ButtonStyle.secondary, row=1)
    async def vol_down_btn(self, interaction: discord.Interaction, button: Button):
        """Abbassa il volume del 10%."""
        new_vol = max(0.05, self.player.volume - 0.10)
        self.player.set_volume(new_vol)
        vol_pct = int(new_vol * 100)
        await interaction.response.send_message(f"🔉 Volume abbassato a: **{vol_pct}%**", ephemeral=True)

    @discord.ui.button(emoji="🔊", label="+10%", style=discord.ButtonStyle.secondary, row=1)
    async def vol_up_btn(self, interaction: discord.Interaction, button: Button):
        """Alza il volume del 10%."""
        new_vol = min(1.0, self.player.volume + 0.10)
        self.player.set_volume(new_vol)
        vol_pct = int(new_vol * 100)
        await interaction.response.send_message(f"🔊 Volume alzato a: **{vol_pct}%**", ephemeral=True)

    @discord.ui.button(emoji="📻", label="Radio: OFF", style=discord.ButtonStyle.secondary, row=1)
    async def radio_btn(self, interaction: discord.Interaction, button: Button):
        """Attiva o disattiva la modalità Radio Infinita (AutoPlay)."""
        self.player.autoplay = not self.player.autoplay
        self._update_button_states()
        await interaction.response.edit_message(view=self)
        stato = "ATTIVATA ✅ (Franchino metterà brani correlati a fine coda!)" if self.player.autoplay else "DISATTIVATA ❌"
        await interaction.followup.send(f"📻 **Modalità Radio AutoPlay {stato}**", ephemeral=False)

    @discord.ui.button(emoji="❤️", label="Salva", style=discord.ButtonStyle.secondary, row=1)
    async def save_fav_btn(self, interaction: discord.Interaction, button: Button):
        """Salva la canzone corrente nella playlist Preferiti del Server."""
        added, song, total = self.player.save_current_to_favorites(interaction.user)
        if not song:
            await interaction.response.send_message("⚠️ Nessun brano da salvare al momento!", ephemeral=True)
            return
        if added:
            await interaction.response.send_message(
                f"❤️ **{interaction.user.display_name}** ha salvato **{song.title}** nei Preferiti del Server! "
                f"*(Totale: {total} brani — dì \"Franchino metti i preferiti\" per ascoltarli!)*",
                ephemeral=False,
            )
        else:
            await interaction.response.send_message(
                f"❤️ **{song.title}** è già presente nei Preferiti del Server! ({total} brani salvati)",
                ephemeral=True,
            )

    @discord.ui.button(emoji="📜", label="Coda", style=discord.ButtonStyle.secondary, row=1)
    async def queue_btn(self, interaction: discord.Interaction, button: Button):
        """Mostra la lista dei brani in coda."""
        embed = self.player.create_queue_embed(page=1)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ==========================================================================
    # RIGA 2: Menu a tendina 12 Effetti Audio DJ in Tempo Reale (Filtri Musica)
    # ==========================================================================
    @discord.ui.select(
        placeholder="🎛️ Filtro Audio DJ sulla musica (12 effetti live)...",
        options=[
            discord.SelectOption(label="Normale (Originale)", value="off", emoji="🎧", description="Audio originale senza filtri"),
            discord.SelectOption(label="Bass Boost (Club)", value="bassboost", emoji="🔊", description="Bassi potenti da discoteca"),
            discord.SelectOption(label="Mega Sub-Bass (Estremo)", value="megabass", emoji="💣", description="Bassi devastanti da festival"),
            discord.SelectOption(label="Nightcore (Dance/Rave)", value="nightcore", emoji="⚡", description="Più veloce e pitch alto stile rave"),
            discord.SelectOption(label="Slowed + Reverb", value="slowed", emoji="🌙", description="Rallentato con riverbero atmosferico"),
            discord.SelectOption(label="8D Audio Surround", value="8d", emoji="🌀", description="Suono rotante a 360 gradi in cuffia"),
            discord.SelectOption(label="Eco / Riverbero Arena", value="reverb", emoji="🏛️", description="Acustica da concerto live in palazzetto"),
            discord.SelectOption(label="Karaoke (Attenua Voce)", value="karaoke", emoji="🎤", description="Riduce la voce centrale per cantare"),
            discord.SelectOption(label="Vaporwave Lo-Fi", value="vaporwave", emoji="🌴", description="Rallentato morbido e nostalgico"),
            discord.SelectOption(label="Voce Scoiattolo (Chipmunk)", value="chipmunk", emoji="🐿️", description="Voce acutissima e divertente"),
            discord.SelectOption(label="Voce Profonda (Demon)", value="demon", emoji="👹", description="Voce ultra-grave e cupa"),
            discord.SelectOption(label="Radio Vintage / Vinile", value="vintage", emoji="📻", description="Effetto vecchia radio anni '60"),
        ],
        row=2,
    )
    async def effect_select(self, interaction: discord.Interaction, select: Select):
        """Applica l'effetto DJ selezionato in tempo reale sul brano in corso."""
        chosen = select.values[0]
        await interaction.response.defer()
        label = await self.player.set_effect(chosen)
        self._update_button_states()
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass
        await interaction.followup.send(
            f"🎛️ **{interaction.user.display_name}** ha impostato l'effetto DJ su: **{label}**!",
            ephemeral=False,
        )

    # ==========================================================================
    # RIGA 3: Soundboard DJ — 12 Effetti Sonori Istantanei nella stanza vocale
    # ==========================================================================
    @discord.ui.select(
        placeholder="📯 Soundboard DJ: spara un effetto sonoro in vocale...",
        options=[
            discord.SelectOption(label="Trombetta DJ (Airhorn)", value="airhorn", emoji="📯", description="Classica tripla trombetta da stadio"),
            discord.SelectOption(label="Sirena Rave Club", value="siren", emoji="🚨", description="Sirena elettronica da discoteca"),
            discord.SelectOption(label="Sub-Bass Drop", value="drop", emoji="💣", description="Esplosione di bassi profondi EDM"),
            discord.SelectOption(label="Rullo di Tamburi (Ba-Dum-Tss)", value="drumroll", emoji="🥁", description="Rullo di suspense con piatto finale"),
            discord.SelectOption(label="Applausi & Ovazione", value="applause", emoji="👏", description="Applausi scroscianti del pubblico"),
            discord.SelectOption(label="Fail / Sad Trombone", value="fail", emoji="🎺", description="Wah-wah-wah-waaaah per figure epiche"),
            discord.SelectOption(label="Fanfara Vittoria", value="victory", emoji="🏆", description="Squillo trionfale da vincitore"),
            discord.SelectOption(label="Laser Space Rave", value="laser", emoji="🔫", description="Raffiche laser sci-fi da console DJ"),
            discord.SelectOption(label="Beep Censura TV", value="censor", emoji="🤬", description="Classico BEEP televisivo di censura"),
            discord.SelectOption(label="Fischio Arbitro", value="whistle", emoji="⚽", description="Fischio deciso da arbitro"),
            discord.SelectOption(label="Risata Sitcom", value="laugh", emoji="😂", description="Jingle comico da battuta"),
            discord.SelectOption(label="Buzzer Errore / Buuu", value="booo", emoji="👎", description="Sirena grave da risposta sbagliata"),
        ],
        row=3,
    )
    async def soundboard_select(self, interaction: discord.Interaction, select: Select):
        """Riproduce subito l'effetto sonoro scelto nel canale vocale e poi riprende la canzone."""
        chosen_sfx = select.values[0]
        await interaction.response.defer()
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass
        from sound_effects import SOUNDBOARD_EFFECTS
        meta = SOUNDBOARD_EFFECTS.get(chosen_sfx, SOUNDBOARD_EFFECTS["airhorn"])
        await interaction.followup.send(
            f"{meta['emoji']} **{interaction.user.display_name}** ha suonato l'effetto **{meta['label']}**!",
            ephemeral=False,
        )
        await self.player.play_sound_effect(chosen_sfx)

    # ==========================================================================
    # RIGA 4: Riavvia Console & Disconnetti
    # ==========================================================================
    @discord.ui.button(emoji="🔄", label="Riavvia Console", style=discord.ButtonStyle.primary, row=4)
    async def restart_btn(self, interaction: discord.Interaction, button: Button):
        """Riavvia e sblocca la console vocale di Franchino."""
        await interaction.response.send_message(
            f"🔄 **{interaction.user.display_name}** ha riavviato la console di Franchino! Riconnessione in corso...",
            ephemeral=False
        )
        self.stop()
        await self.player.restart_console()
        if self.player.text_channel:
            try:
                await self.player.text_channel.send("🎧 **Franchino si è riavviato ed è di nuovo in ascolto!** ✨")
            except Exception:
                pass

    @discord.ui.button(emoji="❌", label="Disconnetti", style=discord.ButtonStyle.danger, row=4)
    async def leave_btn(self, interaction: discord.Interaction, button: Button):
        """Disconnette il bot dal canale vocale."""
        await self.player.disconnect()
        self.stop()
        await interaction.response.send_message("👋 Franchino ha lasciato la console! A presto ragazzi!", ephemeral=False)


class QueuePaginationView(View):
    """Pannello di navigazione per le pagine della coda."""

    def __init__(self, player: "GuildPlayer", current_page: int = 1, total_pages: int = 1):
        super().__init__(timeout=60)
        self.player = player
        self.current_page = current_page
        self.total_pages = total_pages
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.current_page <= 1
        self.next_btn.disabled = self.current_page >= self.total_pages

    @discord.ui.button(emoji="◀️", label="Precedente", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: Button):
        if self.current_page > 1:
            self.current_page -= 1
            self._update_buttons()
            embed = self.player.create_queue_embed(page=self.current_page)
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(emoji="▶️", label="Successiva", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: Button):
        if self.current_page < self.total_pages:
            self.current_page += 1
            self._update_buttons()
            embed = self.player.create_queue_embed(page=self.current_page)
            await interaction.response.edit_message(embed=embed, view=self)
